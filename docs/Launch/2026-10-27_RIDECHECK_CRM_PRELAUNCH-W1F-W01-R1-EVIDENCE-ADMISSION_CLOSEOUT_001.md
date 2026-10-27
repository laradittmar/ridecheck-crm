PROJECT: RIDECHECK_CRM
TYPE: CLOSEOUT
MILESTONE: PRELAUNCH-W1F-W01-R1-R1-EVIDENCE-ADMISSION-PLACEMENT-CORRECTION

# W1F-W-01 R1 — evidence admission (placement corrected)

**Date:** 2026-10-27 · **Starting HEAD:** `83f45b25a7148de5f622d3369e9103e0cdfc00be`
(`fix(prelaunch): W1F-W-01 R3 locality resolution safety`, pushed) · **Branch:**
`integration/prelaunch-booking-convergence` · **Flag:** `EVIDENCE_ADMISSION_ENABLED`,
default **OFF** · **NOT committed. Not built, not deployed, no migration, no live
configuration change.** This is an owner checkpoint, not a landing.

**Revision 2 — owner correction OC-R1 (2026-10-27).** The owner accepted the design, the
six-path scope, the default-OFF flag, the refusal rules, the provenance contract, the
baseline comparison and the mutation evidence *in principle*, and required one change: the
admission execution point moves from **before Layer A** to **immediately after the Layer C
unsupported-service exit and before the Layer D FAQ bypass**. That change is made, the
source-order guard and the tests are extended to prove it, the §9 turn shapes are
re-measured with **dispatch separated from waiting** (six became eight — Layer C and a
locality-free FAQ turn were missing, and the previous revision's "Layer B" row was not in
fact a Layer B exit, §13.8), and §§0, 2, 3, 7, 8, 9, 10, 11, 12, 13 and 15 are rewritten
against the new measurements. Everything below is the corrected system.

The correction is not cosmetic and the numbers say so: with the flag ON, a terminal
pre-gate turn now reads the interpreter **0 times** where the pre-correction placement made
it read once and wait up to 6.0 s (§9, PASS B). What it costs is stated with equal
precision in §9.3 and raised as a new owner decision in §15.7.

---

## 0. What this milestone did, and what it did not do

**Did.** A turn's locality evidence is now decided at a typed admission boundary **before
any gate that continues the conversation can answer the customer and return** — below the
three terminal pre-gates, above the FAQ bypass — and it is written through the chokepoint
that already owns candidate fields. A semantic `INSPECTION_LOCATION` claim has,
for the first time, a decision site to arrive at; every claim family the interpreter
produces now receives a typed decision with provenance, refusals included.

**Did not.** No outstanding-question contract. The system still does not know what it
asked, so Layer F step 9 can still answer an answer: on the Wild's own turn 2 the customer
still receives the qualification greeting. What changed is that the locality the customer
gave **survives** that greeting instead of being destroyed by it. **The complete "en
flores" Wild regression is NOT closed, and R2 is still required.** A test asserts this
(`test_e02`), so the limitation cannot be quietly lost.

**Also did not: no schema change.** The review's R1 plan opens with a new table
(`whatsapp_thread_evidence`, §8.2) plus two candidate foreign keys (§8.3). This milestone
does not create them, for three reasons stated plainly so the owner can overrule them:

1. the milestone prompt forbids migrating any database, and this project's schema-adoption
   path makes a new table a *deployment-ordered* change, not a local one:
   `schema_preflight.EXPECTED_HEAD` requires `alembic_version` to equal the code's single
   head, so an image carrying a new migration **refuses to start** against the current
   `crm_test` until that migration has run (`20261022_operator_notifications` today);
2. R2 needs a table of its own (`whatsapp_outstanding_questions`, §8.1). One migration
   carrying both tables costs one migration window instead of two, and the owner has
   already had to renew the migration window twice (`OC-0`, `P-1-R1`);
3. the boundary itself needs no table. What the table buys is **cross-turn retention** of
   evidence that arrives before its candidate exists — review invariant 3, corpus cases
   `W1F-C-05` and `W1F-C-07`. Those cases are therefore **not closed here**, and §11 says
   so in the same words.

---

## 1. The defect, as measured by the review

> **Authority in the deployed system is positional, not evidential.** What decides a turn
> is where the turn exits the ordered gate chain in `_process_text`.

The Wild, turn by turn: the bot answered three FAQ questions and asked where the vehicle
was; the customer replied `en flores`; the interpreter read
`INSPECTION_LOCATION = Flores, CONFIRMED`; the turn exited at Layer F step 9 — **18 lines
before the only deterministic location writer and 34 before the only semantic location
consumer** — and sent the generic qualification greeting 23.2 seconds later. The locality
was never persisted, and the question was asked three times in one conversation.

Two facts made it unrecoverable, and R1 addresses both:

| fact | review | R1 |
|---|---|---|
| the turn ended before any decision site ran | RC-1, trace 8 `DETERMINISTIC_FLOOR` | admission runs after Layer C and before Layer D |
| no decision site accepted a semantic location claim at all, so the authority flag being ON changed nothing and the refusal left no record | RC-3, G-11 | one typed boundary, one decision per produced family, refusals on the record |

---

## 2. Files changed

| file | status | lines | what |
|---|---|---|---|
| `backend/app/services/evidence_admission.py` | **NEW** | 443 | the boundary: a pure leaf over claims. Eligibility, typed refusal codes, provenance. No ORM, no engine, no provider — asserted structurally (`test_f09`) |
| `backend/app/services/conversation_engine.py` | modified | +292 / −47 | `_admission_on`, `_admission_locality_validator`, `_admit_turn_evidence`, the `_ZoneBuffer` adapter, **the call site between Layer C and Layer D (OC-R1)**, and two predicates extracted to one home each |
| `backend/app/settings.py` | modified | +12 | `evidence_admission_enabled: bool = False` ← `EVIDENCE_ADMISSION_ENABLED` |
| `tests/test_w1f_w01_r1_evidence_admission.py` | **NEW** | 1 097 | 71 tests / 153 subtests: the leaf, the complete turn, the boundaries, flag-OFF, what R1 is not, the structural rules, and **`TestR1G_Placement`** — the eight tests the correction required |
| `tests/test_l4_7w5_g3_1_hybrid_site_trace.py` | modified | +9 / −3 | **one deliberately changed assertion**, see §7.3 |

Net: two new files, three modified, **no migration, no model, no new table, no column**.
The correction needed **no new file**: the eight placement tests are an R1-owned group in
the R1-owned module, so the six proposed commit paths are unchanged (§14).

### 2.1 The two extractions, and why they are in this diff

Both are single-rule consolidations that W1F-W-01's own finding **W1FRV-07** ("detector
logic is duplicated, so a fix to one can silently miss the other") argues for, and both are
flag-independent, which is why §7.2's flag-OFF identity comparison is the evidence that
they changed nothing:

* `_is_current_focus(state, candidate)` — the candidate-location isolation predicate
  (M21.2.14) was written out **three times** inside `_apply_zone_from_text`. Admission is
  the fourth reader. One copy now, four call sites, net −10 lines (`test_f04`).
* `_semantic_binding_refused()` — the G3-5B/G3-5C fail-closed burst binding. Admission
  applies the same rule, so the rule has one home and one log line (`test_f05`).

---

## 3. Architectural placement

```
   handle()
     1  persist + dedup + context + canonical_before snapshot
     2  canonical burst built  (_canonical_turn_burst, G3-5A)
     3  semantic interpreter DISPATCHED  (_run_shadow_understand — async, one call)
        ▲ unchanged by R1: this is where it sits at the starting HEAD, and every turn
          below — including the three terminal pre-gates — still starts one background
          interpretation, exactly as it does today
     4  Layer A motorcycle exit      ─┐  terminal: the turn leaves automation
     5  Layer B phone-call exit       │  no locality persistence needed
     6  Layer C unsupported service  ─┘  and no semantic wait is paid (§9 PASS B)
   ┌─────────────────────────────────────────────────────────────────────────────┐
   │ 7  ADMIT   ◄── NEW, and it is the whole of R1   (OC-R1 placement)           │
   │      A. the deterministic role-aware reader, in WRITE-ONLY mode             │
   │         `_apply_zone_from_text(..., allow_contradiction_return=False)`      │
   │         → LR-2 origin separation, LR-3 vehicle-location authority, the R3   │
   │           coverage/ambiguity pre-gate and candidate isolation, all intact,  │
   │           and it CANNOT send                                                │
   │      B. the semantic claims, at the typed boundary                          │
   │         project → eligibility → `_apply_inspection_zone` (the C2 chokepoint)│
   │      every produced family gets a decision; refusals are recorded           │
   └─────────────────────────────────────────────────────────────────────────────┘
     8  Layer D FAQ bypass        ◄── one of the two gates the Wild measured
     9  Layer G ▸ website/QUOTED ▸ vehicle advisory
    10  Layer F intent gate (9 steps)  ◄── the other one; step 9 greets the Wild's turn 2
    11  zone block ▸ normalisation ▸ price quote ▸ routing gate   (unchanged)
    12  reply model (renderer) ▸ candidate application ▸ clarification ▸ Flow
    13  response plan ▸ OutboundSafetyGate ▸ hybrid trace
```

Four properties of that placement are load-bearing and are asserted:

* **below the three terminal pre-gates** — `test_f07` reads the order off the source as one
  expression, and also pins each gate's own `return` statement above the boundary, so a
  comment moved without its code would not satisfy it. `test_g01` is the behavioural half:
  with the flag ON, a motorcycle, phone-call, transfer or repair turn reads the interpreter
  **0 times** and produces no admission record at all;
* **above every gate that ends a continuing conversation** — Layer D answers and returns,
  and before R1 that was one of the two measured places a locality died (`test_g05`); the
  other is Layer F step 9 (`test_g04`, `test_b01`);
* **below the interpreter's dispatch** — the boundary consumes the interpretation the turn
  already pays for. Measured: `_turn_semantic_calls == 0` on an admitting turn
  (`test_b13`), admission adds exactly **one** read and never a second model call
  (`test_g02`), and the authorised-reader guard in the G3-1 suite still proves no reader
  calls the provider (§7.3);
* **it cannot speak.** `allow_contradiction_return=False` is what makes step A write-only.
  The coverage response and the SC17 contradiction reply are still produced later in the
  turn by the same code as today, because this run never consumes them (`test_f03`,
  `test_b11`, and mutant **M13**).

And one more that the correction asked for as a test rather than as a measurement:
`test_g09` counts SQL statements and commits on the harness engine, ON against OFF, and
requires the terminal pre-gates to be **equal on both** — the boundary may not add a read, a
write or a transaction above them — while the turns that do reach admission add statements
and still no commit, because admission writes inside the turn's own transaction.

### 3.0 Why the boundary sits here and not above Layer A

The pre-correction placement put admission above Layer A, which is where the architectural
review specifies it. It was certified there, and the owner overruled it for a reason the
harness can now confirm with a real provider rather than an injected one.

`SEMANTIC_SAME_TURN_ENABLED=true` on the candidate deployment, so admission's one read of
`_semantic_turn_evidence()` is a **bounded wait** of `SEMANTIC_SAME_TURN_TIMEOUT_SECONDS`
(6.0 s). Layers A, B and C are terminal: motorcycle and phone-call hand the customer to a
person, Layer C states a service boundary. None of them continues the automated
conversation, so none of them needs the locality persisted — and above Layer A, all three
paid that wait. Below Layer C they pay nothing: **0 reads, 0 waits, no admission record**
(§9 PASS B, `test_g01`).

What the move gives up is stated rather than implied: a locality named in the same breath as
a handoff request — "tengo una moto, la revisan? en flores" — is now **not** persisted.
`test_g03` asserts exactly that, because it is a deliberate boundary and not an oversight.
The turn ends with a human either way.

What the move preserves is the measured Wild requirement, and it is the reason Layer D is
the dividing line rather than Layer F: Layer D is the first gate that ends the turn **while
the conversation continues**. `test_g05` measures a FAQ-plus-locality turn keeping
`('CABA','Flores')` with a byte-identical FAQ answer, and `test_g04` measures the Wild's own
turn 2 keeping it through the Layer F greeting.

### 3.1 Why step A exists at all

The review's structural point is that the semantic producer had no decision site. Its
*measured* point is that the turn ended too early — and the reading the turn ended too early
to use was, in the Wild's turn 2, available to the **deterministic** resolver as well:
`_extract_zone_from_text("en flores") = ('CABA','Flores')`, measured by the review on the
deployed code. A boundary that admitted only semantic claims would therefore not have fixed
the measured failure. Step A is the deterministic reading, moved; step B is the semantic
reading, admitted. Both go through chokepoints that already exist.

At the corrected placement this matters more, not less: on a Layer D FAQ turn the
deterministic reader at stage 14 is **never reached** — Layer D returns eight stages above
it — so step A is the only producer that can keep that locality, and `test_g05` measures it
keeping it with no interpretation offered at all.

### 3.2 What writes, and where

| situation | target | writer |
|---|---|---|
| an established current-focus candidate exists | that candidate | `_apply_inspection_zone` |
| no candidate, or only a `mentioned` one | `state.home_zone_*` through `_ZoneBuffer` | `_apply_inspection_zone` |

`_ZoneBuffer` is a four-line adapter that gives the pre-candidate buffer a candidate's
shape, so a value reaching the buffer from admission is **decided by the certified
reconciler** instead of assigned directly — which is what the location form still does and
what finding **W1FRV-04** is about. R1 does not repeat that bypass; it also does not fix the
form (§11).

---

## 4. Admitted evidence schema and provenance

The boundary returns an `AdmissionResult` holding one `AdmissionRecord` per produced claim.
Nothing is persisted in a new table; the records are the turn's typed decision log, written
to `_decision_log` (`evidence_admitted` / `evidence_refused`) and available on the engine as
`_turn_admission`.

```
AdmissionRecord
  claim_type        ClaimType.*            (the family the claim belongs to)
  value             the producer's value    (never a value no producer proposed)
  outcome           ACCEPT | CLARIFY | HOLD (ReconciliationOutcome — unchanged vocabulary)
  reason            a typed AdmissionReason code
  detail            the validator's or the rule's own words
  normalized        (zone_group, zone_detail) — the validator's output, ACCEPT only
  provenance        Provenance

Provenance
  producer            e.g. "semantic:understand/1.20"      ← the evidence SOURCE
  producer_version    the interpreter's model version
  evidence_class      HUMAN_CONFIRMED | EXPLICIT_CUSTOMER | DETERMINISTIC_EXTRACTED |
                      CATALOG_CONFIRMED | SEMANTIC_INFERRED | FUZZY_SUGGESTED | …
  explicitness        STATED | IMPLIED | DERIVED
  status              CONFIRMED | PROPOSED | AMBIGUOUS | CONFLICT
  polarity            ASSERTED | NEGATED
  cycle_id            the thread's cycle identity (one definition, shared with the
                      clarification attempt store)
  turn_id             this turn's correlation id                ← the ORIGINATING TURN
  burst_fingerprint   the G3-5A canonical burst identity        ← WHICH burst
  source_message_ids  the interpreter's own ordered_message_ids ← WHICH MESSAGES
  claim_id            the claim's content-hash id
```

`ClaimEvidence` already carried every one of these attributes in memory; the boundary reads
them and does not invent a field. **Contract:** `evidence-admission/1.0`.

---

## 5. Precedence and rejection rules

### 5.1 What may be admitted

```
ADMITTED_CLAIM_TYPES        = { inspection_location }
ADMISSIBLE_STATUSES         = { CONFIRMED }
ADMISSIBLE_EVIDENCE_CLASSES = { HUMAN_CONFIRMED, EXPLICIT_CUSTOMER,
                                DETERMINISTIC_EXTRACTED, CATALOG_CONFIRMED }
```

One family, because it is the family the Wild lost, the one with a certified validator (the
R3 resolver) and a certified writer. Every other family — including every family that could
advance commercial state — is named in `REFUSED_CLAIM_TYPES` and recorded, never admitted
(`test_a01`, `test_a02`, mutant **M14**). There is no price, slot, offer or booking claim
type in the claim space, and `test_a02` asserts that too: review invariant 6 is preserved by
the shape of the thing, not by a promise.

### 5.2 The refusal codes, each with the rule it names

| code | outcome | rule | killed by |
|---|---|---|---|
| `FAMILY_NOT_ADMITTED` | HOLD | the family has its own authority; this boundary is not a second door into it | M14 |
| `ROLE_NOT_INSPECTION` | HOLD | an origin or a seller location is never the car's location (rule E) | M14 |
| `STALE_CYCLE` | HOLD | a claim from a finished cycle is not evidence about this one | M02 |
| `STALE_TURN` | HOLD | the reading names no message of this turn | M03 |
| `STALE_BURST` | HOLD | the interpretation is not positively bound to this turn's canonical burst (G3-5C, fail closed) | M04 |
| `NEGATED` | HOLD | a denial is not an assertion of something else | — |
| `UNCONFIRMED_STATUS` | HOLD | PROPOSED, AMBIGUOUS and CONFLICT are all "not yet" | M05 |
| `INFERRED_NOT_EXPLICIT` | HOLD | **model prose is evidence, not authority** — see §5.3 | M06 |
| `ROLE_DECIDED_BY_TEXT` | HOLD | the words place the locality with the customer; a model's role label does not overrule LR-2 — see §5.4 | M07 |
| `NO_VALUE` | HOLD | nothing said is NEITHER, never FALSE | — |
| `LOCALITY_UNSUPPORTED` | CLARIFY | the R3 coverage declaration refused it | M10 |
| `LOCALITY_AMBIGUOUS` | CLARIFY | the R3 cross-coverage set refused it (San Nicolás) | M09 |
| `LOCALITY_UNRESOLVED` | HOLD | no catalogue locality, or only part of a longer name | M10 |
| `CONFIRMED_VALUE_PRESENT` | HOLD | an established value is not overwritten; a correction is a different decision with its own confirmation step | M08 |
| `OUTRANKED_BY_EXPLICIT` | HOLD | this turn's deterministic reading of the customer's own words wins | M08 |
| `COMPETING_LOCALITIES` | CLARIFY | two readings of equal strength that disagree are a question, never a coin toss | M11 |

### 5.3 Two rules that were added because a measurement demanded them

Both were found by running the boundary, not by reasoning about it, and both are reported
here because they are the difference between "admission" and "the model decides".

**`INFERRED_NOT_EXPLICIT`.** With inferred localities admitted, a turn that said only
`es un corolla del 2024` — no locality anywhere in the customer's words — acquired an
inspection locality from the interpreter and **was answered with a price for it**. Measured.
The rule is now: a locality whose words are not in the burst is not admitted, whatever the
model's confidence. It keeps the policy the system already has for exactly this evidence —
`_recover_locality` turns an approximate semantic locality into a **proposal the customer
confirms**, never a write. `EXPLICIT_CUSTOMER` is how the projection classifies a mention
whose value appears verbatim in the burst, which is the Wild's own case: the interpreter
reading `Flores` out of `en flores` is reporting the customer's words, not guessing.

**`ROLE_DECIDED_BY_TEXT`.** `soy de flores` and `estoy en flores` are turns where the
role-aware reader correctly writes nothing: the words place the locality with the
**customer**. Before this rule, an interpreter that labelled the same mention
`INSPECTION_LOCATION` had it written as the car's locality — the exact role collision the
review calls RC-5, where the price and the appointment would be computed for the wrong side
of the city. The absence of a deterministic claim is not the absence of a deterministic
decision. Measured both ways (`test_b10b`, and `test_b10c` proves the rule does not swallow
`soy de tigre pero el auto está en flores`).

### 5.4 Where precedence is enforced

Order first, ladder second — one rule each, neither duplicated:

1. **Order.** Step A (the customer's own words, deterministically read) runs before step B
   (the interpretation). A value step A established this turn is never overwritten;
   `OUTRANKED_BY_EXPLICIT` is the refusal that says so by name.
2. **The C2 ladder.** When claims do arrive together, `reconcile_inspection_location`
   chooses — HUMAN_CONFIRMED ▸ EXPLICIT_CUSTOMER ▸ DETERMINISTIC_EXTRACTED ▸
   CATALOG_CONFIRMED ▸ SEMANTIC_INFERRED. The boundary hands it every eligible claim and
   does **not** re-implement the ranking; `_apply_inspection_zone` grew one additive
   keyword (`claims=None`) so the caller can supply what it already holds, and all other
   call sites are unchanged (`test_f06`).
3. **Same-class conflict.** The ladder ranks different classes, not two claims of the same
   class — `_first_value` would take the first, so list order would choose the locality.
   The boundary refuses them all instead (`COMPETING_LOCALITIES`).

---

## 6. The flag

| | |
|---|---|
| name | `EVIDENCE_ADMISSION_ENABLED` → `settings.evidence_admission_enabled` |
| default | **False**, on the dataclass and in the env read (`test_d03`, `test_d04`, mutant M16) |
| armed by | the literal `true` only; a merely truthy value, and a `MagicMock` settings object, do not arm it (`test_d05`, mutant M17) |
| OFF behaviour | one attribute read, then the committed `83f45b2` turn. No admission record exists (`test_d01`) |
| rollback | flag OFF on the same image. Nothing to undo: no table, no column, no migration, and nothing is written that the inherited turn would not also write |
| set anywhere? | **no.** Not in the running container, not in a compose file, not in `.env`. Verified |

---

## 7. Tests, and the baseline identity comparison

### 7.1 Focused runs

Sealed every time: `docker run --rm --network none -e DATABASE_URL=sqlite://
-e PYTHONDONTWRITEBYTECODE=1 -v <tree>:/work:ro -w /work/backend
ridecheck-crm-test:l4.7b2 python -m pytest … -q -p no:cacheprovider`.

| suite | result |
|---|---|
| `test_w1f_w01_r1_evidence_admission.py` | **71 passed, 153 subtests passed** (was 63 / 111; `TestR1G_Placement` adds 8 tests and 42 subtests) |
| the two committed R3 suites + the G3-1 authorised-reader guard | **144 passed, 1 skipped, 553 subtests passed** |
| **83** relevant modules (semantic, conversation-engine, candidate, location, zone, quote, scheduling, claim, reconciler, clarification, intent, FAQ, hybrid-trace, G3 series), flag **OFF** | **3 212 passed, 3 634 subtests, 188 skipped, 4 failed** |
| the same 83, flag **ON** | **3 212 passed, 3 634 subtests, 188 skipped, 4 failed** — failure identities byte-identical to the OFF run |
| the same 83 on a **pristine `git archive 83f45b2`** | **3 212 passed, 3 629 subtests, 188 skipped, 4 failed** — the same four identities |

The four are `test_br1_intent_gate::test_row5_no_catalog_hit_fuzzy_confirm`,
`test_m21_1_1_service_intent_gate::test_si_h1_prior_moto_mention_not_in_current_evidence`,
`::test_si05_revisar_ford_ka_antes_de_comprarlo` and
`test_l4_7w5_g3_7f_owner_resolution::test_or_73_no_booking_revision_is_modified`
(SUBFAILED). All four reproduce on the pristine baseline tree in the same subset, so all
four are inherited. The fourth is also the subject of a correction to the previous
revision's arithmetic — see §7.2.

### 7.2 The whole suite, and the identity comparison the prompt asks for

The baseline was captured by running the suite on a **pristine `git archive 83f45b2`**
export, with full output and `-ra`, in the same image. Identities are compared by **name**,
not by count: every `FAILED`, `ERROR` and `SUBFAILED` line, node id only.

| tree | failed | passed | skipped | errors | subtests | identity lines |
|---|---|---|---|---|---|---|
| pristine `83f45b2` (baseline) | 62 | 6 740 | 646 | 9 | 6 715 | **71** |
| this tree, flag **OFF** (default) | 62 | 6 812 | 646 | 9 | 6 889 | **71** |
| this tree, `EVIDENCE_ADMISSION_ENABLED=true` | 62 | 6 812 | 646 | 9 | 6 889 | **71** |

```
NEW  (in candidate, not in baseline) = []          both postures
GONE (in baseline, not in candidate) = []          both postures
diff(ids_flag_off, ids_flag_on)      = empty       the two candidate sets are identical
```

**No failure or error identity was introduced, none disappeared, and no signature changed.**
+72 passed and +174 subtests over the baseline are this milestone's own tests, and the
OFF and ON runs agree to the row — same failures, same passes, same subtest count.

**A correction to the previous revision, made because the correction's own rerun exposed
it.** That revision reported **70** identity lines and stated they were "61 `FAILED` + 9
`ERROR` + 0 `SUBFAILED`". That was wrong. The honest set is **71** — 61 `FAILED` + **1
`SUBFAILED`** + 9 `ERROR` — and the missing line is
`SUBFAILED(fn='_withdraw_dependent_proposals')
test_l4_7w5_g3_7f_owner_resolution::J_AccountingAndArchitecture::test_or_73_no_booking_revision_is_modified`.
It was dropped by the filter that built the baseline identity file, not by the suite: it is
present in the stored baseline log at line 9046, and rebuilding the baseline set from that
same log with the corrected filter gives 71 lines and `NEW=[] GONE=[]` against both candidate
postures.

Two things are worth saying about it rather than leaving it as a footnote. First, the
conclusion does not change: the comparison was and is clean in both postures, and the
corrected baseline and the corrected candidate sets are equal line for line. Second, this is
a lesson the programme had **already** learned and this milestone lost: the R3 owner review
established "count `ERROR` and `SUBFAILED` lines, not just `FAILED`" precisely because
pytest's summary line (`62 failed, 9 errors`) does not count a subtest-only failure. The
filter is now `^(FAILED|ERROR|SUBFAILED)` with the ` - ` detail stripped, and it is the
filter used for every tree in the table above.

The failure itself is inherited and was verified to be so directly: `test_or_73` asserts
that `inspect.getsource(ConversationEngine._withdraw_dependent_proposals)` does not contain
the string `ThreadRevision`, and that function's text is **byte-identical** in the pristine
baseline tree and in this one (3 461 bytes in both, containing the string in a G3-7P
comment). Run alone, the test fails on the pristine baseline tree exactly as it fails here.

A caveat stated rather than hidden: most suites build the engine with a `MagicMock` settings
object, so the environment variable does not reach them — which is exactly why
`test_d05`/mutant M17 exist, and why the ON posture is *also* exercised per-turn, inside the
R1 module, by setting the attribute explicitly on 71 tests and 153 subtests.

A second caveat, found while chasing the line above: **the identity set of a subset is not
the identity set of the suite.** The 83-module focused run produces four identities where
the full suite produces 71, and one of the four (`test_or_73`) does not appear in the full
suite's list for the baseline capture at all until the filter is corrected. Subset runs are
a fast signal; the full-suite comparison is the authority, and both are reported above
against the same pristine tree.

### 7.3 The one deliberately changed assertion

`tests/test_l4_7w5_g3_1_hybrid_site_trace.py::DomainVerdictGuard::test_r2_m_instrumentation_adds_no_provider_call`
counts the call sites of `_semantic_turn_evidence` in `conversation_engine.py` and pins the
number. It was 8; the admission boundary is the ninth authorised reader, so it is now 9.

This is the guard's own documented pattern — G3-4 added one reader, G3-7 added one, G3-7F
added one, each bumping the count with a comment saying why — and the guard's subject is
unchanged: **no provider call was added.** The evidence for that claim is independent of the
count, and the correction added a second, independent proof of it: `_turn_semantic_calls ==
0` on an admitting turn (`test_b13`) and on all eight placement shapes in both postures
(`test_g07`); and `test_g02` measures that admission adds exactly **one** read of the
memoised accessor — never two, and a second read cannot cost a second model call by
construction. The change is flag-independent, which is why it shows in the flag-OFF
comparison, and it is still the **only** pre-existing assertion this milestone alters.

---

## 8. Mutation run

Each mutant is a real behaviour change, planted one at a time on a copied tree, run against
the R1 module and both R3 modules.

**20 planted, 20 killed, 0 survived** — the 17 of the previous revision, re-run unchanged
against the corrected tree, plus three the correction demanded. A source-order assertion
that no mutant can break is decoration, so the three new mutants **move the admission call**
to each of the three wrong places and nothing else.

| # | mutant | killed by |
|---|---|---|
| **M18** | admission moves back **above Layer A** — the pre-correction placement | `test_f07` + `test_g01` on all five terminal-gate texts |
| **M19** | admission moves **below Layer D**, after the FAQ bypass | `test_f07`, `test_g02`, `test_g05`, `test_g06`, `test_g09` |
| **M20** | admission moves **below Layer F**, after the intent gate | `test_b01`, `test_b02`, `test_b04` — the Wild turn stops persisting again |
| M01 | the admission call site is deleted | `test_b01` (the Wild turn stops persisting) |
| M02 | a claim from another cycle is admitted | `test_a05` |
| M03 | a reading that names no message of this turn is admitted | `test_a06` |
| M04 | an unbound interpretation is admitted | `test_a07` |
| M05 | a `PROPOSED` reading becomes truth | `test_a09` |
| M06 | an inferred locality becomes truth | `test_a16b` |
| M07 | a model role label overrules the text | `test_a16d` |
| M08 | an established value is overwritten | `test_a15` |
| M09 | an AMBIGUOUS locality is treated as resolved | `test_b08` |
| M10 | an UNSUPPORTED locality is treated as resolved | `test_b09` |
| M11 | two equal-strength disagreeing readings are both kept | `test_a18` |
| M12 | a `mentioned` candidate receives the locality | `test_b07` |
| M13 | admission may speak (`allow_contradiction_return=True`) | `test_c01` (the coverage boundary) |
| M14 | every family is admitted | `test_a01` |
| M15 | a refusal leaves no record | `test_a05` |
| M16 | the flag defaults ON | `test_d03` |
| M17 | a merely truthy setting arms the boundary | `test_d05` |

Review §15.6 asks for four specific mutants: dropping the admission call (**M01**), binding
stale-cycle evidence (**M02**, **M03**), closing an outstanding question without a matching
claim (**not applicable — R1 persists no question**, and `test_e01`/`test_e03` assert that),
and writing a zone for an out-of-coverage locality (**M10**).

**M18 is the one worth reading, because it is the correction itself.** Putting the call back
where the architectural review specifies it is not a *wrong* implementation of R1 — it is
the version the owner reviewed — so what kills it is not a correctness assertion but the
order guard plus the latency assertion: `test_f07` reads the order off the source, and
`test_g01` measures a terminal pre-gate reading the interpreter once instead of never. That
pair is what makes OC-R1 a property of the system rather than a comment in a diff.

**M10 is worth reading as a methodology note.** On its first run it SURVIVED — and the
reason was not a missing test but a weak assertion. With the `UNSUPPORTED` branch removed,
the validator falls through to its `verdict != RESOLVED` guard and returns `UNRESOLVED`, so
nothing is written either way and a test asserting only "not admitted" passed. The refusal
*code* was the only observable difference. The assertion was strengthened to pin the exact
code and outcome per locality — `mar del plata`/`cordoba`/`rosario` → `LOCALITY_UNSUPPORTED`
+ CLARIFY, `san miguel del monte` → `LOCALITY_UNRESOLVED` + HOLD — and the mutant then
died. A refusal nobody can read is the invisibility the review named (G-11); this is the
test that makes the codes load-bearing rather than decorative.

---

## 9. What admission costs

Re-measured at the corrected placement, in the sealed SQLite harness. **This is a harness
measurement, not a production one** — it is an order of magnitude, not an SLA. The owner
correction asks for dispatch and waiting to be separated precisely, so there are two passes
and they measure different things.

### 9.1 PASS A — the engine's own work (injected provider, median of 12 turns)

| turn | flag OFF | flag ON | delta | extra SQL | extra commits |
|---|---|---|---|---|---|
| Layer A motorcycle exit | 19.8 ms | 19.0 ms | **−0.7 ms** | 0 | 0 |
| Layer B phone-call exit | 21.8 ms | 20.7 ms | **−1.0 ms** | 0 | 0 |
| Layer C unsupported-service exit | 24.0 ms | 23.5 ms | **−0.5 ms** | 0 | 0 |
| Layer D FAQ exit, locality in the burst | 20.0 ms | 31.8 ms | +11.7 ms | +4 | 0 |
| Layer D FAQ exit, no locality | 17.7 ms | 44.3 ms | +26.6 ms | +2 | 0 |
| Layer F step 9 (the Wild turn) | 24.4 ms | 37.5 ms | +13.1 ms | +3 | 0 |
| full quote turn | 50.6 ms | 51.8 ms | +1.2 ms | +2 | 0 |
| no locality at all (`buenas`) | 17.0 ms | 37.6 ms | +20.6 ms | +2 | 0 |

The three terminal pre-gates now cost **nothing**: the time deltas are measurement noise
around zero and the SQL delta is exactly **+0 statements**, because those turns return above
the boundary and never enter it. Before the correction the same three rows read +27.0,
+28.6 and (for Layer D) +29.0 ms with +2 statements each. Commit counts are identical in
every row of the table — 4 and 4 on the Layer A/B and Wild/quote turns, 3 and 3 on the
Layer C, FAQ and greeting turns — because admission writes inside the turn's existing
transaction and opens none of its own. The statements it does add on the turns that reach it
are the catalogue read the zone block already performs further down the same turn.

### 9.2 PASS B — dispatch versus waiting (real provider, interpreter sleeps 500 ms)

The question the owner asks is not answerable with an injected provider, so this pass runs
the **real** `TurnSemanticEvidence` provider and the real bounded shadow worker, with a fake
interpreter that sleeps 500 ms and returns nothing. Median of 3 turns, worker drained
between turns.

| turn | flag OFF | flag ON | delta | interpreter dispatched? | wait paid? |
|---|---|---|---|---|---|
| Layer A motorcycle exit | 24.5 ms | 34.3 ms | +9.8 ms | **yes** | **no** |
| Layer B phone-call exit | 24.0 ms | 24.4 ms | +0.5 ms | **yes** | **no** |
| Layer C unsupported-service exit | 25.8 ms | 35.8 ms | +9.9 ms | **yes** | **no** |
| Layer D FAQ exit, locality in the burst | 22.7 ms | 526.3 ms | +503.5 ms | yes | **yes — new** |
| Layer D FAQ exit, no locality | 26.1 ms | 523.7 ms | +497.6 ms | yes | **yes — new** |
| Layer F step 9 (the Wild turn) | 525.3 ms | 535.3 ms | +10.0 ms | yes | yes — already at HEAD |
| full quote turn | 531.9 ms | 561.2 ms | +29.3 ms | yes | yes — already at HEAD |
| no locality at all (`buenas`) | 21.2 ms | 531.2 ms | +509.9 ms | yes | **yes — new** |

Read the `dispatched` column before the `wait` column, because that is the distinction the
correction asks for and the one it would be easy to overstate. **The interpreter is
dispatched on every turn, including the terminal ones, in both flag postures, and that is
the starting HEAD's behaviour, not R1's.** `_run_shadow_understand` sits above Layer A at
`83f45b2` and R1 does not move it; `test_g07` asserts `_turn_semantic_dispatched` is True on
all eight shapes in both postures, so the claim cannot later be read as "R1 removed provider
activity from the safety paths". It did not. It removed the **wait**, which is the only part
a customer can feel. Saying "zero provider activity because `_turn_semantic_calls == 0`"
would have been false, and §12 is worded accordingly.

The same table, read honestly, also measures how much of the remaining wait is actually
R1's. On the Wild turn and the full quote turn the wait **already existed at `83f45b2`** —
those turns have inherited readers of the same memoised provider (two and three of them
respectively), so admission's read is free and the delta is +10 ms and +29 ms. The new waits
are the three rows marked *new*.

### 9.3 The cost that remains, stated plainly

**With the flag ON, every turn that reaches Layer D now waits for the interpretation — even
a turn with no locality in it at all.** `buenas` costs 21 ms today and 531 ms with a 500 ms
interpreter; in production the bound is 6.0 s. That is the honest residue of the correction:
it removed the wait from three terminal paths and left it on the FAQ and greeting paths,
which are high-volume.

It is removable, and the measurement points at a one-line way to remove it, which this
milestone deliberately does **not** take unilaterally (§15.7). A locality that does not
appear in the customer's own words can never be admitted — `INFERRED_NOT_EXPLICIT` refuses
it, by name, in every case — so on a burst whose text contains no catalogue-resolvable
locality the semantic read cannot change any outcome. Skipping the read on those bursts
would be behaviour-preserving by construction. It is still a design change the correction
did not ask for, and the rule it would need is a second copy of the projection's
verbatim-mention rule, which is the exact shape of finding W1FRV-07. So it is offered as a
decision with the measurement behind it, not performed.

One further observation, found while building PASS B and reported because it bounds the
worst case: when the single bounded shadow worker is **backlogged**, admission's wait
reaches the full timeout rather than the interpretation's own latency — an early version of
this probe queued jobs faster than the worker drained them and measured 5 030 ms against a
5.0 s timeout. The behaviour at the bound is correct and was designed for (`get()` returns
None, which means *no semantic evidence*, never a guess), so the failure mode is latency and
never wrong evidence. It is still a reason the flag needs its own runtime checkpoint.

---

## 10. What R1 fixes — per case, measured on the complete turn

Every row below was produced by `ConversationEngine.handle()` in the sealed harness, with
the R3 locality safety ON (as the integrated candidate will run it).

| input | flag OFF | flag ON | case |
|---|---|---|---|
| `en flores` (the Wild's turn 2) | thread zone `(None, None)` — destroyed | thread zone **`('CABA','Flores')`**, greeting unchanged | `W1F-C-01/02/04` **partially**: evidence kept, reply still wrong (R2) |
| `en flores`, candidate in focus | zone on candidate, quote sent | identical | `W1F-C-06` regression floor, held |
| FAQ-only burst naming a locality (`¿de qué consta la revisión? en flores`) | nothing persisted | **`('CABA','Flores')`**, FAQ answer byte-identical | the turn-1 half of the defect; `test_g05` |
| the same burst with no interpretation offered at all | nothing persisted | **`('CABA','Flores')`** — step A alone; Layer D returns eight stages above the deterministic reader | `test_g05`, and the reason step A exists (§3.1) |
| `tengo una moto, la revisan? en flores` | nothing | **nothing** — the turn exits at Layer A, above the boundary; 0 interpreter reads | the OC-R1 boundary, `test_g01`/`test_g03` |
| `llamame por favor`, `necesito hacer la transferencia del auto`, `me podes reparar el motor?` (+ the interpreter's `Flores`) | reply, escalation, state as committed | **identical**, 0 interpreter reads, no admission record | `test_g01`/`test_g03` |
| `en flore`, `en florez`, `en tegui` + the interpreter's `Flores` | nothing | nothing — `INFERRED_NOT_EXPLICIT`; stays `_recover_locality`'s confirmable proposal | `W1F-C-20` unchanged, deliberately |
| `en floress` | nothing (turn exits first) | `('CABA','Flores')` — the inherited matcher's ASR pass reads it, so this is deterministic evidence | — |
| text `en flores`, model says `Palermo` | — | `('CABA','Flores')`; the reading refused | precedence, requirement 3 |
| `soy de flores`, `estoy en flores` + model says INSPECTION | nothing | nothing — `ROLE_DECIDED_BY_TEXT` | RC-5 role collision, prevented |
| `soy de tigre pero el auto está en flores` | `('CABA','Flores')` | `('CABA','Flores')` + ADMITTED | LR-2/LR-3 preserved |
| `en san nicolas` | no zone (R3) | no zone, `LOCALITY_AMBIGUOUS` + CLARIFY | `W1F-C-14`, R3 intact |
| `en mar del plata`, `en cordoba`, `en rosario` | coverage response, no zone (R3) | identical + `LOCALITY_UNSUPPORTED` | `W1F-C-15/17`, R3 intact |
| `en san miguel del monte` | no zone (R3) | no zone, `LOCALITY_UNRESOLVED` | `W1F-C-19`-adjacent, R3 intact |
| a reading naming a foreign message id | — | nothing persisted, `STALE_TURN` | staleness |
| a `mentioned` MOTO beside the focus car | MOTO zone `(None, None)` | identical | `W1F-C-22`-adjacent isolation |
| 12 boundary turns (no intent, FAQ, hours, opt-in, vehicle, quote, scheduling, handoff, motorcycle, unsupported service, coverage, ambiguity) | — | **byte-identical** on reply, Flow, both zones, stage, escalation, route, every pricing call, every attempt and revision count — in 12 cases × 3 injected localities | `test_c01`, `test_c02` |

---

## 11. What remains, intentionally, for R2 and beyond

| item | why it is not here |
|---|---|
| **the Wild's turn 2 still gets the qualification greeting** | Layer F step 9 reads the customer's words and finds no service signal. The precondition that stops it — "this turn answered an outstanding question" — needs the outstanding-question contract. `test_e02` asserts the current behaviour so R2 inverts it deliberately |
| **no outstanding-question contract** | R2's table. R1 creates no row, and `test_e01`/`test_e03` assert that the admission record claims nothing about questions |
| **cross-turn retention** (`W1F-C-05`, `W1F-C-07`): a locality given before any candidate exists still does not bind to a candidate created in a LATER turn | needs `whatsapp_thread_evidence` (§8.2) and a migration. `_apply_candidate`'s `RISK-03` guard still — correctly — refuses to inherit an untyped buffer value, and `test_e04` pins that the guard is still there |
| **W1FRV-04**: the location form writes `state.home_zone_*` directly, bypassing the reconciler and never touching the candidate | item 4 of the review's R1 plan. It is a change to the form's write path, independent of the boundary, and it is the natural companion to the evidence table rather than to this order change |
| **G-11 in full**: a trace reconciliation row for *every* produced family | admission logs a typed decision per family to `_decision_log`, and the location family gets its reconciliation row at `location.inspection.apply` as before. Emitting trace rows for scheduling/FAQ/handoff families from a second site would change what the Inspector reads at those sites, which review §15.4 forbids without its own decision |
| **the gate chain still reads raw customer text** | review §8.5's sequencing contract and roadmap C6 (class-D retirement). Explicitly out of scope: "routing stays; it is fed differently" |
| **W1FRV-05 plural clauses, W1FRV-02/03 locality safety** | closed by R3, already committed as `83f45b2` |
| **a locality stated inside a terminal handoff turn is not persisted** — "tengo una moto, la revisan? en flores", a phone-call request, a transfer or repair request | the owner's OC-R1 boundary, taken deliberately so three safety replies are not coupled to a model's completion (§3.0, §9.2). `test_g03` asserts the non-persistence so it cannot be mistaken for a defect later. If a human picks the conversation up, the locality is in the message they read |

An observation found while probing and reported because it is adjacent, not because R1
changes it: `en belgrano r` resolves to `('CABA','Belgrano')` in both postures. "Belgrano R"
is a distinct barrio and is not in the 211-row catalogue, so the matcher reads the prefix.
Pre-existing, unchanged by R1, and not a coverage error (both are CABA, same viáticos
group). Logged for the catalogue, not for this milestone.

---

## 12. Isolation — nothing live was touched

| claim | evidence |
|---|---|
| no commit, no push, no tag, no stash | HEAD is still `83f45b25a7148de5f622d3369e9103e0cdfc00be`, `origin/integration/prelaunch-booking-convergence` is the same commit (0 ahead / 0 behind), `git stash list` is empty |
| the dirty worktree is preserved | the only tracked modification outside this milestone is `docs/operations/RECOVERY_MANIFEST.md`, **unstaged and untouched** — still 20 421 bytes dated 2026-10-23 19:02. The 52 unrelated untracked closeouts and audits are untouched |
| the running backend never saw this code | container `b340087e4cee`, image `ridecheck-crm-backend:prewild-6a14cdb`, **restart count 0**, started `2026-10-26T15:00:30Z` (before R3, let alone R1). Its only bind mounts are `/opt/ridecheck-secrets → /run/secrets` and `/opt/ridecheck-crm-forensics → /run/forensics`; **the worktree is not mounted into it**, so editing files on disk cannot reach the running process |
| flags unchanged | `OUTBOUND_ENABLED=false`, `SMART_BOOKING_CE_ENABLED=false`, `OPERATOR_WHATSAPP_NOTIFY_ENABLED=false`, `CONVERSATION_ENGINE_DIRECT_WEBHOOK_ENABLED=false`, `HYBRID_TRACE_ENABLED=true`. `EVIDENCE_ADMISSION_ENABLED` appears **0 times** in the container's environment, and `LOCALITY_RESOLUTION_SAFETY_ENABLED` is still absent too |
| no database changed | `crm_test` read twice under `default_transaction_read_only=on`, before and after: messages **52** (max id **6239**), traces **12**, ai_events **7**, candidates **1**, zones **211**, security_events **734**, `alembic_version = 20261022_operator_notifications` — every counter identical. `pg_tables` contains **0** of `whatsapp_thread_evidence` / `whatsapp_outstanding_questions`: this milestone creates no table |
| no migration | `backend/migrations/versions/` is unchanged; `schema_preflight.EXPECTED_HEAD` is unchanged; nothing to apply |
| no provider contact | every container ran with `--network none`, so no call could leave the process even if something tried. 0 Meta, 0 OpenAI, 0 SMTP, 0 Resend, 0 n8n calls. The reply model is the patched `urllib.request.urlopen`. Stated precisely, because §9.2 makes the distinction load-bearing: `_turn_semantic_calls == 0` in every test, and in the PASS B probe the interpreter that *is* invoked is a local fake class with no client, no HTTP and no key — `SemanticTurnInterpreter` itself is never constructed there |
| no outbound message | `OutboundSafetyGate` is a mock in every test, `_send_whatsapp_cloud_text`/`_flow` are captured, and no `whatsapp_messages` row is written in any tree |
| no image, no deploy | no `docker build`, no `docker compose`, no container created except the ephemeral `--rm --network none` test runners |

---

## 13. Honest limitations

1. **R1 does not close W1F-W-01.** The gate stays open. The Wild's conversation still asks
   for the location more than once; R1 stops the answer from being destroyed, R2 stops the
   question from being re-asked, and only then does the three-turn sequence replay with one
   location request.
2. **No runtime proof.** No conversation was run, no flag changed, no Flow dispatched. The
   behaviour of the corrected system is measured in a sealed harness, never demonstrated on
   the live path. The review imposes the same limit on itself.
3. **The semantic half of the boundary is proven by injection.** `_semantic_turn_evidence`
   is patched to state what the interpreter said. That is the right boundary for a test, but
   it means these results do not re-derive what `understand/1.20` would actually say about
   any of these bursts.
4. **The semantic branch rarely does the writing.** Because a locality must appear in the
   customer's own words to be admitted, and the deterministic matcher reads the same words,
   the two producers usually agree and step A is the one that writes. The semantic branch's
   distinct contribution in R1 is structural — a decision site now exists where the review
   measured none — plus the cases where the deterministic role logic suppressed a locality
   the customer explicitly wrote. The measured behaviour change on the Wild's turns comes
   from the ORDER, and this closeout does not claim otherwise.
5. **Latency is now measured, and a residue remains.** The correction's PASS B (§9.2) runs
   the real provider with a slow fake interpreter, so the bounded wait is visible rather
   than invisible: it is **absent** on all three terminal pre-gates and **new** on the FAQ
   and greeting paths (§9.3). What is still unmeasured is production: the real interpreter's
   own latency distribution on the live path, under real shadow-worker load, with
   `SEMANTIC_SAME_TURN_TIMEOUT_SECONDS=6.0`. That belongs to the flag's own runtime
   checkpoint and nothing here substitutes for it.
6. **The admission records are not canonical state.** They live in the turn's decision log
   and on the engine object. Provenance is therefore auditable for the turn, not queryable
   afterwards; that is what §8.2's table is for.
7. **`confidence` is still null.** `turn-evidence/1.4` carries the field and
   `understand/1.20` never populates it (W1FRV-09), so no admission rule weighs it. None
   does.
8. **The placement tests name the gates by their own predicates, not by their labels.**
   Building `TestR1G_Placement` turned up that two texts the previous revision treated as
   terminal are not: `quiero hablar con una persona` does not satisfy
   `_is_phone_call_request` (it names no call), and `hacen transferencias?` satisfies none of
   Layer C's three detectors. Both fall through to the full chain. The placement tests
   therefore use texts verified against each gate's own predicate — `llamame por favor`,
   `necesito hacer la transferencia del auto`, `me podes reparar el motor?` — and
   `test_c01`/`test_c02` keep the unverified ones as ordinary boundary cases, which is all
   they ever were. No conclusion in the previous revision depended on the mislabelling, but
   the Layer B row of its latency table did, and §9 is re-measured.
9. **One instrument note.** The R1 module loads the certified R3 full-turn harness as a
   module rather than copying it, so both modules share one SQLite engine and one `app.db`
   stub, and `OUTBOUND_ENABLED` is armed per turn with `patch.dict` — the discipline the R3
   owner review established after a module-scope write cost eight false failures.

---

## 14. Proposed commit — NOT executed

```
git add backend/app/services/evidence_admission.py \
        backend/app/services/conversation_engine.py \
        backend/app/settings.py \
        tests/test_w1f_w01_r1_evidence_admission.py \
        tests/test_l4_7w5_g3_1_hybrid_site_trace.py \
        docs/Launch/2026-10-27_RIDECHECK_CRM_PRELAUNCH-W1F-W01-R1-EVIDENCE-ADMISSION_CLOSEOUT_001.md
```

Six paths. `docs/operations/RECOVERY_MANIFEST.md` and the 52 unrelated untracked documents
are **not** in that list.

```
fix(prelaunch): W1F-W-01 R1 evidence admission
```

---

## 15. Owner decisions required

1. **Authorise the commit** with the six paths and the subject above, or amend either.
2. **Boundary placement — DONE, confirm it reads as ordered.** Admission now runs after the
   Layer C unsupported-service exit and before the Layer D FAQ bypass. The required order
   A < B < C < admission < D < F is pinned by `test_f07` and measured by `test_g01`–`g09`;
   three new mutants (**M18**, **M19**, **M20**) move the call to each of the three wrong
   places and all three die. One consequence is deliberate and is listed in §11: a locality
   stated inside a terminal handoff turn is no longer persisted.
3. **The schema change — ACCEPTED by the owner.** The evidence table and the
   outstanding-question table land together in one additive migration with R2. Recorded
   here; nothing in R1 depends on it.
4. **The one changed assertion — ACCEPTED by the owner**, conditional on the
   no-additional-provider-call evidence remaining valid. It does, and the condition is now
   proved two ways rather than one: `_turn_semantic_calls == 0` on every measured turn
   (`test_b13`, `test_g07`), and admission adds exactly **one** read of the memoised
   accessor, never a second call (`test_g02`). §7.3.
5. **Note what R1 does not close** (§11): `W1F-C-05`, `W1F-C-07`, W1FRV-04, G-11 in full,
   and the Wild's own turn-2 reply. **W1F-W-01 stays OPEN** — the owner has confirmed this.
6. **The flag stays OFF** until a build-and-deployment checkpoint of its own, after R2.
   Nothing in R1 is enabled by committing it, and R1 is not built or deployed separately.
7. **NEW — R1-PL-01 (MEDIUM): the FAQ and greeting paths now pay the bounded semantic
   wait.** §9.3 measures it: `buenas` goes from 21 ms to 531 ms against a 500 ms
   interpreter, and the production bound is 6.0 s. The correction removed this wait from the
   three terminal gates; these two paths are what is left, and they are high-volume. The fix
   is small and provably behaviour-preserving — skip the semantic read when the burst
   contains no catalogue-resolvable locality, because `INFERRED_NOT_EXPLICIT` already
   refuses every claim such a burst could produce — but it is a design change the correction
   did not request, and the predicate it needs duplicates the claim projection's
   verbatim-mention rule, which is the shape of finding W1FRV-07. **Three options, owner's
   call:** (a) accept the latency and close it at the flag's runtime checkpoint;
   (b) authorise the skip inside R1 as a bounded follow-up with its own mutants;
   (c) fold it into R2, where the projection's rule is being touched anyway. Nothing is
   implemented either way in this revision.

   **OWNER DECISION (OC-R1-R1, 2026-10-27): option (c) selected.** The optimization is
   carried into R2, and **R1-PL-01 remains an explicit R2 acceptance requirement** — it is
   not closed by committing R1. Three conditions travel with it, stated here so R2 cannot
   quietly satisfy a weaker version:

   1. R2 must create **one shared predicate** for "could this burst produce an admissible
      locality at all", used by both the claim projection's verbatim/catalogue rule and the
      admission boundary's pre-check. A second copy of that rule is the W1FRV-07 defect and
      is not an acceptable implementation.
   2. The integrated R1+R2+R3 candidate must **prove** that a turn carrying no potentially
      admissible locality evidence does not wait on semantic completion — by the PASS B
      method of §9.2 (real `TurnSemanticEvidence` provider, a fake interpreter that sleeps),
      not by an injected-provider read count alone. `buenas` is the canonical case: it must
      return without the wait, where it costs 531 ms today against a 500 ms interpreter.
   3. The proof must separate dispatch from waiting as §9.2 does. `_turn_semantic_calls == 0`
      is not evidence that no interpretation was started, and R2 may not claim it is.

NEXT: PRELAUNCH-W1F-W01-R2-OUTSTANDING-QUESTION — OWNER AUTHORIZATION REQUIRED

STOP.
