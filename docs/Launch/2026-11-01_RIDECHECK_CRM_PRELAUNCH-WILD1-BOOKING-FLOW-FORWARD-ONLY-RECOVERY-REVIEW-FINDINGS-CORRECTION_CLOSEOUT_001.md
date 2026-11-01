PROJECT: RIDECHECK_CRM
TYPE: CLOSEOUT
MILESTONE: PRELAUNCH-WILD1-BOOKING-FLOW-FORWARD-ONLY-RECOVERY-REVIEW-FINDINGS-CORRECTION

**Project** RIDECHECK_CRM · **Date** 2026-11-01 · **Type** CLOSEOUT
**Parent commit** `b31020ac812a9220cfe3ce39da8800993335de82`
**Commit status** This correction pass staged and committed nothing. The fourteen paths it describes are committed by the separate `…-CORRECTION-COMMIT` milestone that follows it.
**Reviewed worktree** `/opt/ridecheck-crm-integration`, branch `integration/prelaunch-booking-convergence`
**Evidence** `/opt/ridecheck-builds/crm-wild1-r3c1-20261101T205601Z` (consolidated: mutation logs, identity sets, full run logs, the re-prover and the proposed path list; its `CHECKSUMS.sha256` self-verifies) · `/opt/ridecheck-builds/crm-wild1-v77-ownerkit-corrected-20261101T203427Z` (corrected owner kit)

---

## §0 Verdict

The five independent-review findings are applied. One HIGH finding (**R-F01**) is closed by
disarming an owner-facing kit that would have walked the owner back to republishing a
Meta-rejected artifact. The other HIGH (**R-F05**) is closed by making Meta's routing rules
real: **one** implementation, now called from the generator, from
`scripts/validate_flow_json.py` and **from the running process at import**, proven by an
execution test rather than a string check.

The mutation claim is no longer overstated. The set was rebuilt: **11 mutants, 11 killed,
every one on a behavioural test** — including the two the review showed were previously
killed by a signature pin and by an incidental `AttributeError`. Two mutants were *added*
to cover the new call sites, and one of them (`m11`) **survived the first attempt**, which
is exactly why §3 demanded an execution test.

No new HIGH architectural defect appeared, so this does not escalate to an owner decision.
**F3a is not closed and was not attempted.** The server's wire response is unchanged.

---

## §1 Baseline, re-established before editing

| item | measured |
|---|---|
| HEAD | `b31020ac812a9220cfe3ce39da8800993335de82` |
| branch | `integration/prelaunch-booking-convergence` (= origin, 0 ahead / 0 behind) |
| staged | **0** · stash **0** · tags **0** |
| correction diff at entry | sha256 `b195bb5253967184a1c1d7cd5efa9573da4dbfff995bc9f7590a077106260756` — **matches the independently reviewed diff exactly** |
| path set at entry | 11 tracked-modified, 64 untracked |
| `RECOVERY_MANIFEST.md` | mtime **2026-10-23 19:02**, last committed `413bf01` (2026-10-16) — pre-existing, untouched, excluded |
| backend | `sha256:19b3cec208b4d97d0618eec6035c7a9fb3cfd1dc9ebb2000c52e09e425eb44f8`, `b31020ac812a`, restarts **0**, started `2026-10-30T19:17:29Z` |
| postgres / n8n | `5246fd86d829…` / `6ea80570b67b…`, restarts 0, both started `2026-10-21T23:31Z` |
| schema | `20261027_w1f_conversation_memory`, 30 tables |
| seven flags | all literal `false` · `booking_dispatch_allowed()` **False** |
| `CLOSED_BETA_ALLOWED_WA_IDS` | `549***8330` — **unchanged** |
| `crm_test` | `30d4ac4566f4b5968fc44ad69aaa3be75d0c292abbfb25292cf278f6ad8bb393` |

The entry diff hash matching `b195bb52…` is the proof that what was corrected is what was
reviewed. No STOP condition was met.

---

## §2 R-F01 (HIGH) — the dangerous owner kit, disarmed and replaced

### What was wrong, verified rather than repeated

`/opt/ridecheck-builds/crm-wild1-v77-metapub-20261030T231602Z`

- `OWNER_PROCEDURE_v77_META.md` carried **no supersession marker** and its `scp` commands
  fetched `TARGET_v7.7-r3-prefill-refusal.json` **from that directory** — and that copy is
  the **rejected cyclic artifact**, raw `98981193109ce426…`, `DETAILS -> [SUMMARY,
  APPOINTMENT]`, `SUMMARY -> [APPOINTMENT]`.
- `verify_meta_export.py` pinned `53dfe8fe…` as *"the PUBLICATION TARGET … MUST be this"*
  and contained **zero** occurrences of `4ce27122` or `dccd3bc9`. It would have called the
  **correct** Flow a failure and the **rejected** one a success.
- `CHECKSUMS.sha256` certified the rejected copy, so **the kit passed its own integrity
  check while being wrong.**

### History preserved, trap disarmed

Nothing was deleted and nothing was silently rewritten. The three entry-point files are kept
**byte-for-byte**:

| preserved | sha256 |
|---|---|
| `OWNER_PROCEDURE_v77_META.md.ORIGINAL` | `227d6617a4e366e280505a034ca14935bf0da26a822813c8e4381f8179287d7a` |
| `verify_meta_export.py.ORIGINAL` | `43e412fe24a9d1627bcd099ecbea0d3b40bf7241113fee67b171aa7d98918255` |
| `CHECKSUMS.sha256.ORIGINAL` | `e251e824e683070486a769f8e9aa40aeba3fffdcc1fde2aca8147e166d8bacf0` |

`TARGET_v7.7-r3-prefill-refusal.json` is left **unmodified** — it is the evidence of what
Meta rejected — and the regenerated `CHECKSUMS.sha256` now labels it as REJECTED in a
header comment rather than certifying it as a target.

**Every entry point now fails closed or warns before it can claim success:**

- `00_SUPERSEDED_DO_NOT_USE.md` — new, **sorts first in any listing**, names the exact
  reason (*its required hashes describe a Meta-invalid cyclic Flow*), states that the kit
  must never be used for **import, verification, rollback or publication**, and points to
  the corrected kit.
- `OWNER_PROCEDURE_v77_META.md` — replaced by a notice; following it is now impossible.
- `verify_meta_export.py` — replaced by a stub that prints the warning to stderr and
  **exits 2 unconditionally**. Measured: `exit=2` for any argument. It can no longer return
  a success from that path.

### The corrected kit

`/opt/ridecheck-builds/crm-wild1-v77-ownerkit-corrected-20261101T203427Z`

| file | sha256 |
|---|---|
| `OWNER_PROCEDURE.md` | `9cc30a00d4aaeabbd67f80ad685cf106bed96457fdb869121d49c6b0fa9ac18e` |
| `verify_flow_export.py` | `20ea9b0cd2a5d0ccc5369bec8974371135d2bba1c47988059ca6a259d39a2c1c` |
| `PUBLISHED_v7.7_forward_only.json` | `4ce27122a55f3efd4fbae117d620abf34024c5a86343bb23ce878182ba2bbd9d` |
| `README.md` | `cf4cecc8fd98a8d44fd95f5eebdc7d4c88ede18ec67c26b23b5dd1324b6392cf` |
| `SUPERSEDED_KIT_NOTICE.md` | `f51268af9d7ec8d5e5a41a0e3e78e444ec2224ee6256202f5397b9ce13913c3d` |
| `computed_fingerprints_20261101.txt` | `32232f31afb3d4d26541300352e23a6d0d2c4e7a482f6cfd8c6c7c3bb0fadc31` |

The corrected verifier, measured on both artifacts:

| input | routing verdict | pin verdict | exit |
|---|---|---|---|
| `PUBLISHED_v7.7_forward_only.json` (`dccd3bc9…`) | forward-only, one entry, acyclic; **IS** the published graph | `OK` | **0** |
| the rejected cyclic artifact (`53dfe8fe…`) | backward route + no entry screen; **IS NOT** the published graph | `REJECTED` | **1** |

**Reserialisation tolerance, proven by experiment rather than asserted.** A simulated Meta
round trip — top-level keys reordered, four-space indent, no trailing newline — produces a
completely different raw hash and the same canonical one:

```
  raw sha256:  4a6907adfc682a053c8038eed76fea058178330b3aeb1bdc95c611ff1e444a84   (differs)
  canonical:   dccd3bc973b1b51526c5064027b5d8f61ee885293dfa9815a272609092aca17d   (matches)
  verdict:     OK — "the export IS the published forward-only v7.7 artifact."   exit 0
```

It satisfies each stated requirement: canonical comparison (so Meta's reserialisation is
tolerated, as measured above); identifies the forward-only artifact; rejects the cyclic artifact **by name**
and as `REJECTED`, never as target or rollback; rejects **v7.6 by name** — and v7.6 is
`REJECTED` rather than merely superseded, because it is **cyclic too**
(`501d4aab…`, see §4); rejects r1, r2, v7.4 and v7.3 by name with `STOP`; prints the Flow
ID `28104222025943520` and the expected routing graph on every run; and separates
*"published artifact matches"* from *"runtime recovery proven"* in so many words.

**No fingerprint was typed from memory, truncated or invented.** All eight were computed
from the files they name and are recorded in `computed_fingerprints_20261101.txt`. The
kit's `CHECKSUMS.sha256` covers the **procedure and the verifier**, not only the payload —
the specific defect that let the old kit certify itself.

The procedure states plainly that the Flow is **already published** and that the existence
of a kit is not a reason to republish anything.

---

## §2b The obsolete v7.6 generator, neutralised (commit-manifest blocker 1)

The commit-manifest verification found `meta/flows/booking/make_minimal_target.py` **still
live and unmarked**. Measured before the change: it ran, **exited 0**, and wrote an artifact
whose canonical hash is `501d4aab43502730a0b176f3fb9cc37173f85e18ed2ecf265ace39795c6745dc`
— exactly `TARGET_v7.6` — with

```
routing_model: {"APPOINTMENT": ["DETAILS"], "DETAILS": ["SUMMARY", "APPOINTMENT"], "SUMMARY": ["APPOINTMENT"]}
```

It never called any validator. Run the canonical validator on its output and you get exit 1
with Meta's backward-route error. Its docstring presented it as current: *"Derive the minimal
refusal-capable Flow from the PUBLISHED artifact."* That is the same hazard as R-F01 — an
unmarked tool leading to a Meta-invalid artifact — but inside Git.

### What it is now

Executable behaviour **removed outright**, not commented out and not flag-guarded: a
commented body is still a body, and a future reader can uncomment or copy it. The file
remains at its path with a superseded header. Measured:

| invocation | result |
|---|---|
| with `src` and `dst` (the old convention) | **exit 2**, stderr message, **no file written** |
| with no arguments | **exit 2** |
| imported instead of run | raises **`SystemExit(2)`** |
| stdout | **0 bytes** — no JSON generated or returned |

The message begins exactly `STOP: TARGET_v7.6 is superseded and Meta-invalid.` and names
both reasons (cyclic/backward routing **and** the wrong baseline), the replacement generator
`meta/flows/booking/make_r3_refusal_target.py`, the authoritative artifact
`meta/flows/booking/TARGET_v7.7-r3-prefill-refusal.json`, its raw hash
`4ce27122a55f3efd4fbae117d620abf34024c5a86343bb23ce878182ba2bbd9d` and canonical hash
`dccd3bc973b1b51526c5064027b5d8f61ee885293dfa9815a272609092aca17d`.

**History is preserved, the working tree is not.** The original generator is in Git at blob
`8b93343d835d099819f8d161c86172ea00bcad80`, commit `c26f980`, recoverable with
`git show c26f980:meta/flows/booking/make_minimal_target.py`. The file says so, and says the
working tree must not reproduce it. By AST there is **no assignment** mentioning
`routing_model` anywhere in it, and no `json.dumps`/`json.loads` at all.

> Note for traceability: earlier closeouts record this file's sha256 as
> `126f2f91866c7208…` and mark it UNMODIFIED. That was true then, and it is confirmed — the
> HEAD blob hashes to exactly
> `126f2f91866c72082e8838b506bd6cf5e0c03b90180a6e0e8944842c3781522e`. The working tree now
> holds the neutralised wrapper,
> `cabf188c8e908b0ca73484d3a66c4718cbebbbfb7c6aa93ecf26e347ec4c2a36`, and **this is the
> change that altered it.** Anything citing `126f2f91…` as current is describing the state
> before 2026-11-01.

### Pinned by nine tests, and documentation corrected

`TestObsoleteV76GeneratorIsNeutralised` (R3-N01…N09) asserts the file still exists with a
superseded header; exit 2 and **no output file even when given an output path**; exit 2 with
no arguments; `SystemExit(2)` on import; **no active backward-edge assignment, by AST**; that
it names the authoritative artifact, generator and both hashes; that **no reference presents
it as usable**; that `TARGET_v7.6` is retained **and** fails routing validation; and — the
regression that matters — that **the active v7.7 generator still reproduces the authoritative
artifact byte-for-byte**.

Three references that still read as active were corrected: `meta/flows/booking/README.md`
called it *"The v7.6 generator"*, the v7.7 generator's docstring said it *"derives"* v7.6 in
the present tense, and `flow_contract.py` described it as audit evidence without noting the
neutralisation. The README's v7.6 row now states both grounds for inadmissibility. R3-N07
enforces this going forward over `backend/`, `scripts/`, `meta/` and the booking README.

---

## §3 R-F05 (HIGH) — one routing validator, actually invoked

### Architecture: one implementation, four callers

`backend/app/services/flow_contract.py` holds the only copy:

- **`assert_forward_only(routing_model, *, screens=None)`** — the rules.
- **`assert_required_forward_graph(routing_model, *, screens=None)`** — the rules **plus**
  the asset pin, so a legal-but-wrong DAG is still refused.

It raises `ValueError`, never `AssertionError`, so `python -O` cannot disable it.
`flow_contract.py` has **no runtime imports** beyond `__future__`, which is what makes it
loadable by a standalone script without dragging in FastAPI or SQLAlchemy.

| call site | how | before |
|---|---|---|
| the v7.7 generator | `FC.assert_required_forward_graph(doc["routing_model"], screens=[…])` after loading `flow_contract.py` by path | kept its **own second copy** of the rules (`_assert_forward_only_dag`), now deleted |
| `scripts/validate_flow_json.py` | new `routing_findings()` → `assert_forward_only`; `check()` puts ROUTING findings first; a ROUTING finding **forces exit 1** | examined **no routing property at all** |
| the running backend | `_assert_forward_only(_ROUTING_MODEL)` at **module import** in `flow_data_exchange.py` | defined, called from **nowhere** outside tests |
| the repository test path | `TestCanonicalRoutingValidator` (13 tests) incl. an **execution** test | three artifact-invariant tests that fired for no mutant |

No second copy of the graph rules exists. `test_r3_v12` asserts the generator no longer
defines `_assert_forward_only_dag` and that the script does not reimplement the inbound
count.

### Rejection battery — all eleven, measured

| rejected input | message |
|---|---|
| `DETAILS -> [SUMMARY, APPOINTMENT]` | *"Backward route [DETAILS->APPOINTMENT] corresponding to forward route [APPOINTMENT->DETAILS] is not allowed in the routing model. Only forward routes can be specified."* — Meta's words |
| `SUMMARY -> [APPOINTMENT]` | *"No entry screen found in the routing model…"* — Meta's words |
| the whole rejected graph | the backward-route error |
| a self-loop `DETAILS -> DETAILS` | backward/cycle |
| **two** entry screens | *"more than one entry screen in the routing model: …"* — **our** words, deliberately not Meta's |
| **zero** entry screens | Meta's words |
| a route **to** an unknown screen | `route TO unknown screen 'NOPE' from 'DETAILS'` |
| a route **from** an unknown screen | `route FROM unknown screen 'GHOST'` |
| a declared screen absent from the graph | `screen 'SUMMARY' is declared but absent` |
| a duplicate route entry | `duplicate route APPOINTMENT -> DETAILS` |
| a non-list route value / empty graph | typed shape errors |

Accepted: exactly `APPOINTMENT -> DETAILS`, `DETAILS -> SUMMARY`, `SUMMARY -> ()`.
A **legal but different** DAG passes `assert_forward_only` and is refused by
`assert_required_forward_graph` — which is why both exist.

One wording correction made here: the zero-entry case keeps Meta's verbatim sentence; the
many-entries case now reports in **our** words, because Meta never gave us that error and
quoting it would have been putting words in Meta's mouth. The pre-existing test
`test_r3_m05` asserted Meta's wording for the many-entries case and has been corrected,
with the reason in its docstring.

### Execution proof, not a string check

```
$ python -I scripts/validate_flow_json.py <the rejected cyclic artifact>
ROUTING   routing_model
          Backward route [DETAILS->APPOINTMENT] corresponding to forward route
          [APPOINTMENT->DETAILS] is not allowed in the routing model. Only forward
          routes can be specified.
1 routing, 0 error(s), 0 render, 2 unproven          → exit 1

$ python -I scripts/validate_flow_json.py meta/flows/booking/TARGET_v7.7-r3-prefill-refusal.json
0 routing, 0 error(s), 0 render, 2 unproven          → exit 0
```

`TARGET_v7.6-minimal-refusal.json` also exits **1** on the routing rule. Every historically
clean artifact — v7.3, v7.4, r1, r2, r3 and the published v7.7 — reports **0 routing
findings**.

And the runtime enforces its own graph: a tree copy with `DETAILS: ("SUMMARY",
"APPOINTMENT")` in `FORWARD_ROUTES` now **fails at import** with Meta's own error text
instead of serving a graph Meta would reject.

**This is local validation. It is not Meta validation and it is not handset validation.**
The script says so on every run, and now adds that a clean ROUTING result is not a handset
result either.

---

## §4 R-F02 – R-F05 disposition

| finding | severity | kind | disposition |
|---|---|---|---|
| **R-F01** | HIGH | evidence kit | **APPLIED** (§2). Old kit marked, entry points fail closed, originals preserved; corrected kit generated. |
| **R-F02** | MEDIUM | documentation (active file) | **APPLIED**. The generator's module docstring declared its own contract as *"C. two routing edges — DETAILS -> APPOINTMENT, SUMMARY -> APPOINTMENT"* while the body asserted the opposite. The header now states the delta is four fields and two components **and nothing else**, and records that the edges were rejected and why. |
| **R-F03** | LOW | documentation (code comments) | **APPLIED**. `TRIGGER_ORIGIN` and `validate_recovery_return` no longer claim every argument is server-derived. `trigger` **is client data**; `origin` is a server-side function of it. The three measured consequences are recorded, including that this is hypothesis **C** for the ten failures and must not be called impossible. `meta/flows/booking/README.md` carried the same overclaim and is corrected. **No code change** — the screen remains entirely server-selected. |
| **R-F04** | LOW | documentation | **APPLIED**. The runbook banner's *"Everything else in this document stands"* is replaced: the title's *"NOT EXECUTED"* is corrected, the *"same two routing edges"* fallback is marked **impossible**, and the return to APPOINTMENT is marked **not proven**. |
| **R-F05** | HIGH | code + test | **APPLIED** (§3). |
| **R-F06** | MEDIUM | test quality | **APPLIED** (§6). Mutation set rebuilt; the overstated claim is corrected in place in the 2026-10-30 closeout. |
| **R-F07** | MEDIUM | evidence narrative | **APPLIED** (§5). Hypothesis E recorded; risk **M2** annotated. |
| **R-F08** | MEDIUM | operations doc | **DEFERRED, not silently.** `RECOVERY_MANIFEST.md` is three deployments stale. It is **excluded by instruction** from this milestone and from the commit, and editing it here would contradict §10. **Owner:** the next controlled deployment or probe-preparation milestone, which has to touch it anyway. It is not HIGH: the manifest is a recovery aid, the live posture is fully recorded in the deployment closeouts, and no automation reads it. |
| **R-F09** | INFO | none | **CLOSED, no action.** The live image's `TARGET_FLOW_SHA256 = 98981193…` is read by no runtime code (`grep` over `/app/app/` finds only its definition). It resolves on the next build. |

**No HIGH finding is deferred.**

### A finding the review did not have

`TARGET_v7.6-minimal-refusal.json` is **also cyclic** — `DETAILS -> [SUMMARY,
APPOINTMENT]`, `SUMMARY -> [APPOINTMENT]`, canonical `501d4aab…`. It was previously
dismissed only as *"derived from the wrong baseline"*. It is inadmissible for **both**
reasons, it is now classified `REJECTED` in the corrected verifier, and the new routing
check exits 1 on it. Recorded as **C1-F01 (LOW)** — a latent trap, not a live one.

### And one about the test suite, not the correction

`tests/test_l4_7w5_agenda_cobro_action.py` fails **15 tests every Sunday**, for any code.
`TODAY = date.today()` at line 60 feeds `week=TODAY.isoformat()` into
`render_calendar_page`, which emits no cell for a Sunday, so `_card()` returns `''` and
fifteen assertions compare against an empty string. **Proven**, not inferred: rendering the
same fixture across a week gives card lengths 1849/1849/1849/**0**/1859/1859/1859 for
Thu–Wed. Recorded as **C1-F02 (LOW)**; it is unrelated to this correction and is **not**
fixed here, because §10 excludes unrelated test changes.

---

## §5 Corrected evidence narrative

A correction notice is added to the head of the 2026-10-30 correction closeout. **Nothing in
its body is rewritten**; the notice lists what is wrong and the body stands as the record of
what was believed. It states, accurately:

- The ten 2026-10-29 handset failures were **not** ten valid slot-conflict recovery
  observations.
- **Six** `booking_attempt_events` rows, all `PRE_EXECUTE / REFUSED /
  SMART_BOOKING_DISABLED`, 11:56:23Z–12:02:17Z. Row 1 is the `08:56:23` ART `BOOKING_REFUSED`
  in the Wild timeline — ledger and narrative are the same events. All six have
  `hist_thread_revision_id` empty: **no booking on any attempt**.
- **Four** were `data_exchange` with an **empty trigger**, re-raised as **HTTP 500** by the
  then-current `except Exception` (WILD1-F4) — **no encrypted body, no screen named**.
  Six plus four is the ten the owner saw, and **only six bear on F3a**. None was a genuine
  availability conflict.
- Meta's publication/preview traffic was **two `action=ping` requests and nothing else**,
  both `200 OK`, 23:36:40Z and 23:36:55Z. **No screen was rendered.** Independently,
  `has_refusal` and `has_error` carry `__example__: false`, so `Ejecutar` cannot draw either
  conditional component even in principle. **MF-R01 is closed for validation only.**
- **F3a is therefore untested, not disproven.**
- **Hypothesis A** — Meta may reject a response screen that is not forward-reachable.
- **Hypothesis E** — Meta may reject returning from a **terminal** screen. `SUMMARY` is the
  only `terminal: true` screen in r3 and v7.7 alike; both non-terminal origins worked and the
  terminal one failed 6/6.
- **A and E predict the same handset outcome, and neither is closed by any local test.**
- A dedicated fourth `REFUSAL` screen (`APPOINTMENT → DETAILS → SUMMARY → REFUSAL`, terminal,
  no route back) is a **contingent architecture option, not an authorised change**.
- The fallback that relied on returning to APPOINTMENT via "the same two routing edges" is
  **impossible** and is not described as proven anywhere.

The same corrections are applied to `meta/flows/booking/README.md`, which previously said the
Wild "returned `SUMMARY → APPOINTMENT` ten times".

### Both historical risks annotated

| risk | where | annotation |
|---|---|---|
| **M3** — *"a validator enforcing an acyclic routing graph rejects both edges"* | 2026-10-29 publication-preparation closeout | already annotated 2026-10-30: ⚠️ **"THIS IS WHAT HAPPENED … The risk was identified here and was not acted on."** Left as written. |
| **M2** — *"`routing_model["SUMMARY"] = ["APPOINTMENT"]` where **SUMMARY is `terminal: true`** … and this edge is the one the Wild actually needed"* | same table, row above | **annotated now**: ⚠️ **"THIS MAY ALSO BE WHAT HAPPENED — STILL OPEN 2026-11-01."** It is hypothesis E, its runtime form was never considered, and **unlike M3 it is not closed.** |

Both original row texts are preserved verbatim. Nothing was rewritten as if it had never
been claimed.

---

## §6 Honest mutation results — the set was rebuilt

The review's three objections are accepted in full:

- **m04 was inert.** `_client_target` defaulted to `None` and **no call site passed it**, so
  `None or response_dict.get("screen","")` was the original expression. Its two kills were
  signature-set assertions, not behaviour.
- **m08 had no behavioural kill.** `test_r3_x11` says so itself ("the structural half"), and
  `test_refusal_07` failed because its `_Stub.__init__` never set `self.db`, so the injected
  `flush()` raised `AttributeError`. The row-count tests did not fire: a commit of nothing
  changes no row.
- **m06 was authorisation-neutral** — an unrecognised outcome was still refused one step
  later by the allowlist lookup — so it died on an error-message assertion.
- **`x01`/`x02`/`x03` are artifact-invariant.** They build a mutant graph from a literal
  inside the test. They are sound unit tests of the validator and they are **not** mutant
  detectors; they fired for no mutant, then or now.

### The rebuilt set: 11 mutants, 11 killed, every kill behavioural

| # | mutation | killed by | why this is behavioural |
|---|---|---|---|
| m01 | artifact: reintroduce `DETAILS -> APPOINTMENT`, **then re-pin both hashes, `FORWARD_ROUTES` and the new import assertion** | `test_r3_m06`, `test_r3_c05`, `test_r2_b03`, `routing_02/03/05` (15) | the pins are neutralised first, so it dies on the routing rule and on the artifact↔runtime tie, not on a hash |
| m02 | artifact: reintroduce `SUMMARY -> APPOINTMENT`, re-pinned | same (15) | as m01 |
| m03 | three extra `RECOVERY_RETURNS` rows | `test_r3_r02`, `r09`, `x04`, `r2_r04` (5) | the allowlist is enumerated exactly; a broadened allowlist authorises a return nothing produces |
| **m04** *(new)* | **the guard stops guarding when an outcome is present** — refusals are encrypted and emitted unchecked | **`test_r3_c04`, `test_r3_g02`** (5) | `g02` *calls* `_guarded_return` with real AES material and requires `HTTPException` 500; the mutant emits a 200 instead |
| m05 | unknown trigger returns instead of raising | `test_r3_r04`, `x06` (2) | an unknown trigger authorises a return |
| **m06** *(rebuilt)* | **an unrecognised outcome now AUTHORISES APPOINTMENT** — both the outcome-set check and the allowlist default are defeated | `test_r3_r02`, `r05`, `g03` (7) | the mutant genuinely permits a generic outcome; `g03` observes the guard emitting instead of refusing |
| m07 | six `raise BookingTokenError(...)` → `pass` | `test_r3_x10`, **`test_r2_r03`** (2) | `r2_r03` calls the real service and requires the exception |
| **m08** *(rebuilt)* | **the refusal path persists a `booked` `ThreadRevision` before refusing**, inside `_refusal_screen` after the `ctx is None` return, so every real refusal writes | **`test_r3_w01_a_slot_conflict_writes_no_booking`** + 10 more | a **row count** changes; this is the danger the old mutant only gestured at |
| m09 | remove the slot-conflict permission | `test_r3_r01`, `r09`, `r10`, `w01`, `x08` (9) | the one genuine recovery stops being authorised |
| **m10** *(new)* | **delete the runtime import-time assertion** (graph left correct) | `test_r3_v11` (1) | only a test that the validator **is called** can see this; it is the R-F05 regression |
| **m11** *(new)* | **`validate_flow_json.py` stops checking routing** (definition left in place) | `test_r3_v13` (1) | the execution test runs the script; **this mutant SURVIVED the string-check-only version of v12**, which is precisely why §3 required an execution test |

`mutate.py` and all eleven logs: ``/opt/ridecheck-builds/wild1r3c1-mutfinal-204356``.

**Honest headline: 11/11 killed, 11 behaviourally.** The 2026-10-30 result, restated
honestly in that closeout's correction notice, was **9/9 killed, 6 of them behaviourally**.
A hash pin, an import failure, a signature assertion or a syntax error is not behavioural
proof, and none of the eleven kills above rests on one.

To make the behavioural kills possible, 30 tests were added:
`TestGuardIsNotBypassable` (8) exercises `_guarded_return` with real AES key and IV, so
"the guard refuses rather than emits" is measured on the function;
`TestCanonicalRoutingValidator` (13) covers the eleven rejection rules, the published-graph
pin, the import-time call site and the script execution test; and
`TestObsoleteV76GeneratorIsNeutralised` (9) pins the v7.6 generator's neutralisation (§2b). `test_r3_c01` was also
tightened: it previously rejected only a base expression containing `data`, so
`payload.get("target")` would have slipped past it.

---

## §7 Test and identity results

### Focused suites, on the frozen tree

| suite | before this milestone | after |
|---|---|---|
| `test_wild1_forward_only_recovery_contract.py` | 71 passed | **101 passed** (+30) |
| the three `test_wild1_*` contract suites | 154 | **184** |
| `test_l4_7w5_gate_a_flow_prefill_contract.py` | 26 | **26** |
| **four suites together** | 180 | **210** |

Forward-routing negative tests: the eleven-case rejection battery in §3, plus
`TestCanonicalRoutingValidator` (13 tests) including the subprocess execution test.

Generator regenerated **twice**: both runs byte-identical to each other **and** to the
published artifact, raw `4ce27122a55f3efd4fbae117d620abf34024c5a86343bb23ce878182ba2bbd9d`,
canonical `dccd3bc973b1b51526c5064027b5d8f61ee885293dfa9815a272609092aca17d`, 12276 bytes.

### Full-suite identity comparison, on frozen trees

Two trees under ``/opt/ridecheck-builds/wild1r3c1-frozen-20261101T204513Z``: `pristine/` is `git archive HEAD`, `correction/` is the same
archive with exactly the twelve changed paths overlaid (and `RECOVERY_MANIFEST.md` restored
to its HEAD content, so the frozen correction tree *is* the proposed commit content).
`diff -rq` between them reports exactly those twelve differences and nothing else.

| run | pytest `failed` | `errors` | subtests passed | tests passed | unique node-ID identities |
|---|---|---|---|---|---|
| pristine `b31020ac`, flags OFF | 79 | 9 | 7098 | 7020 | **85** |
| **correction**, flags OFF | 79 | 9 | 7098 | 7112 | **85** |
| pristine `b31020ac`, integrated | 83 | 9 | 7098 | 7016 | **89** |
| **correction**, integrated | 83 | 9 | 7098 | 7108 | **89** |

```
OFF         pristine vs correction    NEW: (none)   GONE: (none)   identity files BYTE-IDENTICAL
INT         pristine vs correction    NEW: (none)   GONE: (none)   identity files BYTE-IDENTICAL
```

**NEW and GONE are both empty in both postures, with the identity files byte-identical.**
Passing tests rise by exactly the size of the new module: **7020 → 7112 = +92** with
flags OFF and **7016 → 7108 = +92** integrated — the 71 tests the reviewed correction
added plus the 30 added here.

> **Two measurement passes, stated rather than buried.** The four-run identity comparison
> above was taken on frozen trees created **before** the v7.6-generator neutralisation
> (§2b). Three things postdate that freeze: the neutralised `make_minimal_target.py`, the
> nine `TestObsoleteV76GeneratorIsNeutralised` tests, and the documentation corrections to
> the three references that still read as active. A comment-only edit to
> `make_r3_refusal_target.py` also postdates it (ASTs byte-identical under `ast.dump`).
>
> Rather than argue that none of it can matter, the full suite was **re-measured on the
> post-neutralisation tree**, both postures:
>
> | posture | failed | errors | passed | identities | vs pristine |
> |---|---|---|---|---|---|
> | OFF | 79 | 9 | **7121** | **85** | NEW 0, GONE 0 — **byte-identical** |
> | integrated | 83 | 9 | **7117** | **89** | NEW 0, GONE 0 — **byte-identical** |
>
> Identical identity sets to the frozen measurement *and* to pristine, in both postures.
> Passing rises by **+9** over the frozen correction tree in each posture — exactly the nine
> `TestObsoleteV76GeneratorIsNeutralised` tests — and by **+101** over pristine, the whole
> new module. The v7.6 neutralisation introduced no failure identity and removed none.
>
> The four focused contract suites (**210 passed**) and the eleven-mutant re-verification
> (**11/11 killed**, same primary killers) were both run on the current worktree, not the
> frozen copy.

### Method, stated so the numbers can be checked

- **Failure count ≠ identity count.** pytest's `N failed` counts **per-subtest** failures;
  the `-rfE` short summary prints one line per **node ID**. Both are reported above.
- Identities are node IDs parsed **space-safely** — cut at the first `" - "` after the node
  id — so the four identities under `tests/Whatsapp Test/Messages/test_conversation_flows.py`
  are counted as **four**, not collapsed into one. That collapse is the instrument fault the
  2026-10-30 author found in their own run; this parser cannot reproduce it.
- Postures: OFF is the bare runner; integrated adds exactly `SMART_BOOKING_CE_ENABLED`,
  `EVIDENCE_ADMISSION_ENABLED`, `OUTSTANDING_QUESTION_ENABLED` and
  `LOCALITY_RESOLUTION_SAFETY_ENABLED`. `OUTBOUND_ENABLED` is never set. Both trees run
  `DATABASE_URL=sqlite://` with `--network none`, so **no database- or network-gated suite
  differs between them.**

### The population difference against 2026-10-30, named exactly

The 2026-10-30 closeout recorded **71** identities OFF and **74** integrated; today the
counts are higher. The difference is **not** "the calendar" in the abstract, and it is not a
collection change. Named:

**Every extra identity is in one file: `tests/test_l4_7w5_agenda_cobro_action.py` — 15 of
them**, across `EndToEnd` (1), `LegacyPagoState` (5) and `RenderedAgenda` (9). The GONE set
against 2026-10-30 is **empty**. 70 + 15 = 85 OFF, and 74 + 15 = 89 integrated.

That file is **tracked, last committed `8d8293c` (2026-09-15), and byte-identical to HEAD**.
Nothing was added or removed from the suite. The cause is in the fixture:

```
line 60   TODAY = date.today()
line 397  _render()  → revision at turno_fecha=TODAY, render_calendar_page(week=TODAY.isoformat())
line 410  _card()    → regex finds no agendaApptCard  →  returns ''
```

`render_calendar_page` emits no cell for a Sunday, so the card is never drawn and fifteen
assertions compare against an empty string. **Proven by experiment**, not inferred —
rendering the same fixture for each day of this week gives card lengths:

```
  Thu 2026-10-29  1849      Fri 2026-10-30  1849      Sat 2026-10-31  1849
  Sun 2026-11-01     0  ←   Mon 2026-11-02  1859      Tue 2026-11-03  1859
  Wed 2026-11-04  1859
```

2026-10-30 was a **Friday**; today is a **Sunday**. Recorded as **C1-F02 (LOW)**: a latent
test-suite defect that fails 15 tests every Sunday for any code. It is **not** fixed here —
§10 excludes unrelated test changes — and it means absolute identity totals are only
comparable **within the same weekday**. The paired same-day comparison above is immune to it,
which is why it, and not the absolute count, is the admissible measurement.

One more instrument note on the 2026-10-30 figures: its two OFF identity files contain
**71 lines but only 70 node IDs** — one line is a stray log message
(`BOOKING_OFFER_ISSUED_WHILE_DISPATCH_OFF …`) that the extraction let through. The integrated
files hold 74 real node IDs. So that closeout's "71 OFF" is **70**, and the arithmetic above
uses 70.

---

## §7b Producer inventory — re-derived by AST on the corrected tree

Ten screen-bearing producers, from `booking_flow_service.py` (1332 lines) and
`flow_data_exchange.py` (278 lines). The raise-site counts are unchanged by this pass:

```
BookingSlotConflictError      _confirm_booking      x4   [880, 947, 1006, 1051]
BookingTokenError             _confirm_booking      x5   [852, 892, 900, 931, 1044]
BookingTokenError             resolve_context       x5   [316, 346, 351, 356, 362]
BookingTokenError             parse_booking_token   x3   [213, 216, 222]
BookingAlreadyCompletedError  resolve_context       x1   [337]
```

| # | producer | origin | trigger | typed outcome | response | writes before returning | in `RECOVERY_RETURNS` |
|---|---|---|---|---|---|---|---|
| P1 | `handle_init` L735 | *none* | `INIT` | — | APPOINTMENT | reads only | n/a |
| P2 | `handle_date_selected` L747 | APPOINTMENT | `date_selected` | — | APPOINTMENT (self-refresh) | reads only | n/a |
| P3 | `handle_prepare_summary` L795 | DETAILS | `prepare_summary` | — | SUMMARY | reads only | n/a |
| P4 | `_confirm_booking` OFF switch L880 | SUMMARY | `confirm_booking` | `BookingSlotConflictError` | APPOINTMENT | refusal evidence + handoff; **no booking, offer or message** | ✅ |
| P5 | `_confirm_booking` `NO_CANONICAL_LOCATION` L947 | SUMMARY | `confirm_booking` | `BookingSlotConflictError` | APPOINTMENT | as P4 | ✅ |
| P6 | `_confirm_booking` `DX_TRANSACTION_NOT_CLEAN` L1006 | SUMMARY | `confirm_booking` | `BookingSlotConflictError` | APPOINTMENT | as P4 | ✅ |
| P7 | `_confirm_booking` authority refusal L1051 | SUMMARY | `confirm_booking` | `BookingSlotConflictError` | APPOINTMENT | as P4 | ✅ |
| P8 | `_success_response` L1094 | SUMMARY | `confirm_booking` | — | SUCCESS | **the booking**, via `BookingAuthority` only | n/a (terminate) |
| P9 | route `BookingTokenError` L243 | per trigger | any of the four | `BookingTokenError` | APPOINTMENT | **nothing** | ✅ ×4 |
| P10 | route `BookingAlreadyCompletedError` L207 | per trigger | any | replay | SUCCESS | nothing — the booking already exists | n/a (terminate) |
| — | `health_response` L1321 | — | `ping` | — | **no `screen` key** | nothing | n/a |

Two conclusions the inventory forces, both unchanged: **there is no slot-conflict producer
on `prepare_summary`** — all four raises are inside `_confirm_booking`, whose only caller is
`handle_confirm_booking`, reached only from SUMMARY — so the milestone's conditional
permission for `DETAILS → APPOINTMENT` was correctly **not** granted; and `TOKEN_INVALID` is
reachable from **every** trigger because `resolve_context` is each handler's first statement,
which is also why the expired-token screen carries no identity.

---

## §8 The recovery contract, re-proved after the tooling corrections

Every item re-measured on the corrected tree. **All pass.**

| # | invariant | how proved |
|---|---|---|
| 1 | Meta navigation remains a forward-only DAG | `FORWARD_ROUTES` passes the canonical validator and **equals** the published graph |
| 2 | `RECOVERY_RETURNS` is a server-side allowlist, not a routing graph | **5** entries; **every one resolves to APPOINTMENT**; no recovery target is a forward route from its own origin |
| 3 | no client field selects a response screen | AST over both files: **zero** reads of `screen`/`target`/`next_screen`/`next`/`destination`/`dest`/`goto`/`to_screen`/`response_screen` from anything but a server-built dict; `validate_response_screen`'s parameters are exactly `{trigger, response_screen, routing_model, outcome, origin, terminal_payload}` — no `data`, `payload`, `request` or `body` |
| 4 | unknown origin / trigger / outcome fail closed | six separate refusals: unknown trigger, unknown outcome, wrong origin for the trigger, unlisted triple, a screen the triple does not name, and `validate_response_screen` on an unknown trigger |
| 5 | `prepare_summary` has no slot-conflict return | no `(DETAILS, prepare_summary, SLOT_CONFLICT)` entry exists |
| 6 | `confirm_booking` is the only slot-conflict producer | all **four** `BookingSlotConflictError` raises are in `_confirm_booking` — lines **880, 947, 1006, 1051** — whose only caller is `handle_confirm_booking` |
| 7 | refusal paths write no booking | `_refusal_screen` contains no `execute` / `commit(` / `BookingAuthority` / `add(` / `flush(`; **and** `test_r3_w01` counts rows before and after a real refusal |
| 8 | duplicate confirm creates at most one booking | `test_r3_w06`: two real `handle_confirm_booking` calls, `before + 1` rows both times |
| 9 | payload phone values cannot replace the server identity | `appointment_payload` **refuses** an undeclared `phone` key; the identity keys are written only by the builder |
| 10 | SUCCESS requires a real extension payload | refused without one, allowed with one, **refused under any refusal outcome**; and `"SUCCESS"` appears **nowhere** in the published artifact, so it is outside the graph by construction |
| 11 | ping names no screen | `health_response()` returns `{version, data:{status}}`; it is also the only raw `_encrypt_and_return` caller |
| 12 | the corrected JSON is unchanged | raw `4ce27122a55f3efd4fbae117d620abf34024c5a86343bb23ce878182ba2bbd9d`, canonical `dccd3bc973b1b51526c5064027b5d8f61ee885293dfa9815a272609092aca17d`, `routing_model` = the published graph |

**The server's wire response is unchanged.** Nothing in this correction touches what the
backend emits: the review established that the deployed `b31020ac` image already supplies
exactly the sixteen APPOINTMENT keys the published v7.7 declares and already sets
`has_refusal`, and no change here alters a payload, a screen or a status code. The
corrections are a validator, its call sites, documentation and tests.

---

## §9 Second tester — recorded, not configured

| role | E.164 | canonical digits |
|---|---|---|
| existing tester | — | `549***8330` |
| second tester | ``549***7590`` | `549***7590` |

**Not configured.** `CLOSED_BETA_ALLOWED_WA_IDS` is still `549***8330`, verified in the
live container at both ends of this milestone. No Compose file, environment file or allowlist
was touched.

Read-only admission checks (from the independent review, re-stated here as the record):
a whole-row text scan of **all 30 `crm_test` tables** for `***7590` and for the bare local
part `***7590` (local part) returns **zero hits** — no contact, thread, lead, candidate, offer, booking,
Flow attempt, outstanding question, evidence row, outbound message or security event, and no
prior appearance under any other normalised form. It is not an operator
(`OPERATOR_WHATSAPP_TO` unset), not a professional or sender (`WHATSAPP_PHONE_NUMBER_ID` is
an asset id), and appears in **no** environment variable. **Not verifiable from here:** that
the handset is owner-authorised and available at GO.

When admission is authorised it is `CLOSED_BETA_ALLOWED_WA_IDS=549***8330,549***7590`
(comma-separated, parsed at `settings.py:198`, enforced at `routes/whatsapp.py:264`) **plus a
backend recreate**, because `get_settings` is `lru_cache`d. That gate is **inbound only** —
outbound is not allowlist-gated — so two live testers widen the outbound surface and the
probe must bound it explicitly rather than assume the allowlist does.

### The intended controlled **two-handset same-slot** probe, recorded and not executed

- admits **exactly** these two testers and no others;
- gives each an independently valid booking context;
- both attempt the **same** slot within a controlled interval;
- **at most one** booking commits;
- the losing request must exercise the **genuine typed slot-conflict path** — not the OFF
  switch, and never deliberate token expiry;
- the loser writes **no** booking;
- **at most one** operator notification, and only after the winning commit;
- nothing is sent to any recipient outside the two testers and the configured operator;
- all runtime flags return **OFF** immediately afterwards;
- **does not count toward the clean-Wild counter, which remains 0/3.**

Worth carrying forward: this race is the one way to produce a real
`BookingSlotConflictError` on `confirm_booking` from SUMMARY **without** deliberate token
expiry, which is exactly the typed condition the F3a probe needs. **The loser's handset is
the F3a observation**, so the concurrency probe and the F3a probe should be one run.

**Privacy.** Neither full number is written into this Git-tracked closeout. They appear
here only as `549***8330` and `549***7590`. The exact digits remain in operational evidence
and configuration **outside this commit** — the live `CLOSED_BETA_ALLOWED_WA_IDS`, the
2026-11-01 independent-review audit (untracked), and the evidence directories under
`/opt/ridecheck-builds`. This is a deliberate departure from earlier practice in this
repository, where the existing tester's number appears unmasked in files already tracked at
HEAD; new tracked documents mask it.

---

## §10 Scope and proposed commit

### Exact final path list — 14 paths

| # | path | change |
|---|---|---|
| 1 | `backend/app/services/flow_contract.py` | the canonical validator (`assert_forward_only` rebuilt to nine rules + `assert_required_forward_graph`); R-F03 docstring corrections |
| 2 | `backend/app/routes/flow_data_exchange.py` | import-time `_assert_forward_only(_ROUTING_MODEL)` — the runtime call site |
| 3 | `scripts/validate_flow_json.py` | **NEW in this milestone**: `routing_findings()` calls the canonical validator; ROUTING findings force exit 1; `--pin-published-graph` |
| 4 | `meta/flows/booking/make_r3_refusal_target.py` | its own copy of the rules **deleted**; calls `assert_required_forward_graph`; R-F02 docstring corrected |
| 5 | `meta/flows/booking/TARGET_v7.7-r3-prefill-refusal.json` | **unchanged from the reviewed state** — carried for the commit |
| 6 | `meta/flows/booking/README.md` | owner-kit safety section; F3a narrative corrected; R-F03 overclaim removed |
| 7 | `tests/test_wild1_forward_only_recovery_contract.py` | **NEW file**, 101 tests (+30): `TestGuardIsNotBypassable` (8), `TestCanonicalRoutingValidator` (13), `TestObsoleteV76GeneratorIsNeutralised` (9), tightened `c01`, corrected `m05` |
| 8 | `tests/test_wild1_r3_aligned_flow_contract.py` | from the reviewed correction |
| 9 | `tests/test_wild1_booking_flow_gate_and_contract.py` | from the reviewed correction |
| 10 | `docs/operations/2026-10-29_BOOKING_FLOW_PUBLICATION_RUNBOOK.md` | R-F04 banner correction |
| 11 | `docs/Launch/2026-10-29_…GATE-AND-REFUSAL-CONTRACT-CORRECTION_CLOSEOUT_001.md` | superseded banner (reviewed correction) |
| 12 | `docs/Launch/2026-10-30_…R3-BASELINE-ALIGNMENT-CORRECTION_CLOSEOUT_001.md` | superseded banner (reviewed correction) |
| 13 | `meta/flows/booking/make_minimal_target.py` | **NEW in this pass**: the obsolete v7.6 generator, neutralised — exits 2, writes nothing (§2b) |
| 14 | *this closeout* | **NEW** |

### Explicitly excluded, and why

| excluded | reason |
|---|---|
| `docs/operations/RECOVERY_MANIFEST.md` | pre-existing (mtime 2026-10-23, last committed `413bf01`), excluded by instruction. Still dirty in the worktree; untouched. **R-F08 deferred, not silently.** |
| the 2026-11-01 independent-review **audit** | audits are untracked in this repository by convention, and §10 does not list it. It is the only committable-adjacent file containing the two tester numbers. |
| the 2026-10-29 publication-prep closeout (M2 annotation) and the 2026-10-30 correction closeout (correction notice) | both **untracked** at HEAD, so they join no commit — the annotations live on disk and in the evidence directories |
| 62 other untracked Launch documents | unrelated |
| Compose files · environment files · migrations · models · runtime configuration · nginx · the allowlist | **none touched**; `grep` over the path list returns 0 for each |
| timestamped runtime evidence directories | not repository artifacts; `/opt/ridecheck-builds/…` is never committed |

Verified mechanically: the 14-path list contains **0** matches for `RECOVERY_MANIFEST`,
`docker-compose`, `.env`, `alembic`, `migrations/`, `nginx` or `CLOSED_BETA`, and 65 other
dirty/untracked paths remain outside it.

### Checks run

| check | result |
|---|---|
| `git diff --check` | **clean** |
| conflict markers in the changed set | **none** |
| staged-set simulation (`git add --dry-run --pathspec-from-file`) | resolves to **exactly the 14 paths**; **0 staged** afterwards |
| secret / token / bearer / WAMID scan over the changed set | **none** (patterns: `EAAG…`, `Bearer …`, `wamid.…`, literal `WHATSAPP_TOKEN=`/`SMTP_PASSWORD=`, `re_…`, `sk-…`, `AKIA…`) |
| live destinations in committable files | only synthetic numbers — `5491100000000`, `5491100000001`, `5491155551234`, `5491199999999`. **Neither tester number appears in any tracked file in the commit** |
| focused contract suites | **210 passed** across four suites |
| forward-routing negative tests | 11/11 rejected, 2/2 accepted, pin rejects a legal-but-wrong DAG |
| generator regeneration ×2 | byte-identical to each other and to the published artifact |
| baseline identity comparison | §7 — **0 NEW, 0 GONE**, both postures |
| mutation | **11/11 killed, all behavioural** |
| §8 recovery-contract re-proofs | **12/12 pass** |

### Proposed commit — NOT EXECUTED

```bash
cd /opt/ridecheck-crm-integration
git add -- \
  backend/app/services/flow_contract.py \
  backend/app/routes/flow_data_exchange.py \
  scripts/validate_flow_json.py \
  meta/flows/booking/make_r3_refusal_target.py \
  meta/flows/booking/make_minimal_target.py \
  meta/flows/booking/TARGET_v7.7-r3-prefill-refusal.json \
  meta/flows/booking/README.md \
  tests/test_wild1_forward_only_recovery_contract.py \
  tests/test_wild1_r3_aligned_flow_contract.py \
  tests/test_wild1_booking_flow_gate_and_contract.py \
  docs/operations/2026-10-29_BOOKING_FLOW_PUBLICATION_RUNBOOK.md \
  "docs/Launch/2026-10-29_RIDECHECK_CRM_PRELAUNCH-WILD1-BOOKING-FLOW-GATE-AND-REFUSAL-CONTRACT-CORRECTION_CLOSEOUT_001.md" \
  "docs/Launch/2026-10-30_RIDECHECK_CRM_PRELAUNCH-WILD1-BOOKING-FLOW-R3-BASELINE-ALIGNMENT-CORRECTION_CLOSEOUT_001.md" \
  "docs/Launch/2026-11-01_RIDECHECK_CRM_PRELAUNCH-WILD1-BOOKING-FLOW-FORWARD-ONLY-RECOVERY-REVIEW-FINDINGS-CORRECTION_CLOSEOUT_001.md"

git commit
```

Subject:

```
fix(WILD1-R3): separate Meta forward routing from typed recovery returns
```

**No `git add .`, no `git add -A`.** The commit is not executed by this milestone.

---

## §11 Live isolation

| item | at entry | at close |
|---|---|---|
| backend image | `sha256:19b3cec208b4d97d0618eec6035c7a9fb3cfd1dc9ebb2000c52e09e425eb44f8` | identical |
| deployment_id / restarts | `b31020ac812a` / **0** | identical |
| backend started | `2026-10-30T19:17:29Z` | identical |
| postgres / n8n container ids | `5246fd86d829…` / `6ea80570b67b…` | identical |
| schema head | `20261027_w1f_conversation_memory` | identical |
| seven flags | all `false` | all `false` |
| `booking_dispatch_allowed()` | **False** | **False** |
| `CLOSED_BETA_ALLOWED_WA_IDS` | `549***8330` | **unchanged** |
| `crm_test` snapshot | `30d4ac4566f4b5968fc44ad69aaa3be75d0c292abbfb25292cf278f6ad8bb393` | **identical** |
| pending outbound | 0 | **0** |
| `operator_notifications` | **1 total** — the pre-existing `SENT` row of 2026-10-26 14:57 (booking_command 8, thread 2074, from the W1F operator-signal Wild) | **1 total; 0 new, 0 unsent.** No notification was created by this milestone, and the identical snapshot proves it |
| `security_events` max id | 735 | **735** |

Every database read used `default_transaction_read_only=on`. All test execution was in
`docker run --rm --network none` containers against read-only mounts of **copied** trees;
the live stack was never a test target.

**Nothing was committed, staged, pushed, tagged, stashed, built, deployed, migrated or
published. No container was recreated. No Compose or environment file was edited. No flag
was changed. The allowlist was not modified. Meta was not contacted. No Flow was sent. No
probe was begun. Nothing was written to `crm_test`. The published Meta Flow is unchanged.**

---

## §12 Remaining F3a hypotheses, and what is still open

**F3a remains OPEN.** Nothing in this milestone attempted to close it, and nothing local can.

| | hypothesis | prediction | status |
|---|---|---|---|
| **A** | Meta rejects a response naming a screen not forward-reachable from the current screen | the refusal screen does not render | **open** |
| **E** | Meta rejects a response returning from a **terminal** screen (`SUMMARY` is the only one) | the refusal screen does not render | **open** |
| **B** | the envelope / schema / data / status was invalid | — | **removed going forward**: the deployed backend supplies exactly the sixteen keys v7.7 declares, and two components now bind the refusal flags |
| **C** | the wrong screen or trigger was recorded | — | largely excluded for the six ledger rows; `trigger` is client-supplied (R-F03) so it cannot be dismissed on principle |
| **D** | another backend or Meta defect | — | **open** |

**A and E predict the same handset outcome**, so one observation cannot separate them — and
it does not need to, because they share one remedy: a forward-reachable, non-terminal path
for the refusal. The smallest forward-only shape is a fourth screen,
`APPOINTMENT → DETAILS → SUMMARY → REFUSAL`, with `REFUSAL` terminal because a route back
to APPOINTMENT is backward and forbidden — which matches the copy that already exists
(`BOOKING_HANDOFF_TEXT`, *"Un asesor se contacta a la brevedad."*). **That is contingent,
not authorised, and not implemented.**

### Open

| item | state |
|---|---|
| **F3a / MF-R04** | OPEN — only a handset probe can decide it |
| **W1F-W-01** | OPEN |
| risk **M2** | OPEN (hypothesis E) · risk **M3** closed by Meta |
| **R-F08** | deferred to the next deployment/probe-preparation milestone (`RECOVERY_MANIFEST.md` three deployments stale) |
| **C1-F01** | LOW — `TARGET_v7.6` is cyclic as well as wrong-baseline; now caught and classified REJECTED |
| **C1-F02** | LOW — `test_l4_7w5_agenda_cobro_action.py` fails 15 tests every Sunday, pre-existing |
| MF-R01 | closed **for validation only**; rendering unproven |
| MF-07 | contained, fail-closed; deliberate token expiry still forbidden |
| the 13 paths | **uncommitted** |
| clean-Wild counter | **0/3** |
| also outstanding | R-14 privileges · operator destination and template configuration · POST-LAUNCH-HARDENING-01 · disk at 88% · residual anonymous volume `bcb587ffc59a…` |

**Local tests passing is not authorisation.** This milestone authorises no build,
deployment, activation or natural Wild.
