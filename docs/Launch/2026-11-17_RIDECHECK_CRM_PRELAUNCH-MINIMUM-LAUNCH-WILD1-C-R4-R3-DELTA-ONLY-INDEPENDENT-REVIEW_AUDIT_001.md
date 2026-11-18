PROJECT: RIDECHECK_CRM
TYPE: AUDIT
MILESTONE: PRELAUNCH-MINIMUM-LAUNCH-WILD1-C-R4-R3-DELTA-ONLY-INDEPENDENT-REVIEW

# WILD1-C-R4-R3 (RV-F-01 motorcycle cross-turn handoff + RV-F-02 pin): delta-only independent review

- **Date.** 2026-11-18, 02:07Z–02:50Z (2026-11-17 23:07–23:50 ART).
- **Gate.** L4 runtime certification (ACTIVE). Scope: the R4-R3 delta only. WILD1-C, R3, R4, R4-R1 and R4-R2 were not re-reviewed; they need regression evidence only (§7).
- **Candidate.** Uncommitted tree on base `aa2c8db08d4e3bf6a1a70acdc48b7d23f4077008`, branch `fix/prelaunch-wild1-conversation-correction`, worktree `/opt/ridecheck-crm-wild1-correction`.
- **Evidence supplied.** `$E7=/opt/ridecheck-builds/crm-wild1-c-r4-r3-20261118T003912Z`.
- **Reviewer artefacts (durable).** `$RV=/opt/ridecheck-builds/crm-wild1-c-r4-r3-review-20261118T0210Z/`. It holds the snapshot `cand/`, the production diff `ce.diff`, the probe `probe/p_rev.py` with its runner and outputs, the PostgreSQL runner and logs in `pg/`, the suite runner, junit and comparator in `ev/`, and the mutants and results in `mut/`. Its artefact manifest `rev_manifest.sha256` (86 files) has sha256 `f8fabd15d23fd378096ce233786754316c93291b2cb89d63d4ec12f87fefa1ef`.
- **Read in full.**
  - The R4 final focused review audit, sha256 `24a867768b631148871c6f5e1f7fe9727becc4425580ded2c65e784eb935edea`.
  - The R4-R3 closeout, sha256 `22c102ef3f1f8dda31240fe465324a74962be7985c5d8158c9f49a3c223f2946`.

**VERDICT: READY FOR CONTROLLED CONSOLIDATION**

RV-F-01 is independently closed. On the real `/api/conversation/handle` route, the following was reproduced on SQLite and PostgreSQL 16, in the sync and live async postures, for moto, cuatriciclo and UTV:

- the first enquiry still gets the existing contact-data Flow;
- every later turn (all six follow-ups), every fallback form and every website form is the existing plain-text human handoff;
- no turn reaches a quote, acceptance, offer, booking token, Booking Flow, revision, ledger row or booking;
- the pending contact Flow survives the handoff and can still be submitted exactly once;
- the review alert fires exactly once.

The car path is unchanged. The suite comparison shows NEW 0 and GONE 0 outside the new file. 16 reviewer mutants were run: 14 are killed by the candidate's suite, and the 2 survivors are killed by my probe. Those 2 survivors are a **LOW test-pin gap** (RV-R3-01, §6). Production behaviour is correct, so the gap is not a defect.

This audit authorizes no commit, push, build, kit update, deployment, Meta change, tester contact, booking-11 action or WILD.

---

## 1. A. Isolation and manifest — **PASS**

| Check | Result |
|---|---|
| HEAD / branch | `aa2c8db08d4e…` / `fix/prelaunch-wild1-conversation-correction`, at start and at end |
| Index | empty (0 paths), at start and at end |
| Candidate manifest | `sha256($E7/cand_manifest.sha256)` = **`82f477e095fcddd9af0c09e270b2143b354e5a6dca239fdc8ae54d59bac1c2c0`** (exact match). It lists 60 `.py` paths, and `sha256sum -c` passes against the worktree at start and at end. |
| Porcelain | Equal to `$E7/porcelain.txt` plus one entry, the R4-R3 closeout. The closeout was written at 21:40 ART, after the porcelain capture at 21:39. The porcelain was byte-identical at the start and end of this review. |
| Other writers | Three `claude` processes have this worktree as cwd: pid 3678493 (the R4 final reviewer, 18:35 ART), pid 3732465 (19:50 ART, the R4-R3 implementer) and this session. The other two had **no child processes** throughout, and no worktree file is newer than the manifest except the closeout. |
| Snapshot | Taken before any run: `rsync` to `$RV/cand` (excluding `.git` and `__pycache__`), with a per-file hash list `cand_full.sha256` (769 files). It was re-verified at the end: unchanged. At the end, `diff -rq` between the worktree and the snapshot shows no difference. |
| `$E7/pre` | Verified independently. `sha256sum -c` against the reviewed R4-R2 manifest `1f714030…` passes inside `pre/`, and `diff -rq` against `$E6/cand` differs only in the added `tests/test_wild1_c_r4_r3_motorcycle_cross_turn.py`. The implementer copied that file in at 21:38 ART for the fails-before run; it is byte-identical to the candidate's. **So `pre` = the reviewed R4-R2 candidate + the new test file.** |

**Independently derived delta** (`diff -rq` of `$E7/pre` against the snapshot, over the whole tree):

- Production: **only `backend/app/services/conversation_engine.py`**, +112/−5 (`$RV/ce.diff`, sha256 `ee7a8d1a…`).
- Tests: `test_br1_intent_gate.py`, `test_l4_7w5_g3_7f_owner_resolution.py`, `test_w1f_w01_r1_evidence_admission.py` and the new `test_wild1_c_r4_r3_motorcycle_cross_turn.py`. The manifest grew 57 → 60 paths by exactly the two previously untouched pin files and the new suite.
- Docs: the R4-R3 closeout only.

**Protected scope.** These are byte-identical between `pre` and the candidate (`cmp`/`diff -rq`):

- `models.py`, `settings.py`, `pricing.py`, `outbound_safety_gate.py`, `offer_lifecycle.py`, `booking_authority.py`, `flow_contract.py`, `booking_flow_service.py`;
- `backend/migrations/`.

The `booking_authority.py` delta against base is the reviewed R4-R2 +8/−1, which R4-R3 did not touch. No Flow definition, Meta asset or n8n workflow changed. PostgreSQL head stays `20261104_vehicle_identity`.

**Writes by this review.** This audit file only. Every run used `:ro` mounts of `$RV/cand`, `$E7/pre` or a throwaway mutant copy, with `PYTHONDONTWRITEBYTECODE=1`, `-p no:cacheprovider`, and either `--network none` or an `--internal` network.

## 2. B. Production patch review — **PASS**

Read in full against `pre`, with the surrounding code (`_handle`, `_process_flow_response`, `_process_text` up to Layer B, `_motorcycle_human_handoff`, `_process_motorcycle_contact_response`, `_load_context`, `_reload_active_candidates`, `_execute_cycle_reset`, `OfferLifecycle.withdraw`/`_clear_mirror`, the four commercial gates, and `_send_fallback_human_review_notification`).

| # | Requirement | Finding |
|---|---|---|
| B.1 | A recorded MOTO candidate or a pending token is thread authority | `_thread_motorcycle_recorded` returns True for a `str` token starting with `moto-handoff-`, or for a candidate in `ctx.candidates` whose `tipo_vehiculo.upper()` is in `{MOTO, MOTOCICLETA}` and whose status is not `archived`. `ctx.candidates` is loaded under the cycle watermark (`_load_context`, and `_reload_active_candidates` after a reset). The reset archives candidates and withdraws through `OfferLifecycle.withdraw`, which clears the mirror unconditionally (`offer_lifecycle.py:563`). A new cycle therefore starts unflagged. It reads no prose and no model output. Every motorcycle path records `tipo_vehiculo="MOTO"`, including cuatriciclo and UTV. |
| B.2 | The first enquiry still gets the contact Flow | The thread check precedes the current-turn check in Layer A, but it is False on a first enquiry. The `_is_motorcycle_enquiry` branch and every other first-identification caller (vehicle fallback, website form, AI path, G3-7F-R2 correction) pass no `recorded`, so the Flow branch is unchanged. Reproduced in §3. |
| B.3 | A later turn cannot forget the motorcycle | Layer A's thread check is the first routing decision in `_process_text` after shadow dispatch and pending-resolution dispatch, which only read. It returns before Layers B/C, the website-form parser, quote, acceptance and scheduling. `_handle` routes only two message types (`flow_response`, text). Flow responses are covered by the moto-token routing and the new fallback-form gate. A booking-shaped submission on a moto thread finds no ISSUED offer (§3 D6b). |
| B.4 | No later automatic quote, acceptance, offer, Booking Flow or booking | The primary gate is Layer A (text) plus the `_process_flow_response` fallback-form gate. Second-line gates: `_compute_price_quote` (MOTO focus or recorded thread → None); `_handle_quoted_acceptance` and `_handle_website_form` (both before any write); the legacy issuer and `_send_booking_flow` (both after `booking_dispatch_allowed`). Each was proven load-bearing in isolation (§5). |
| B.5 | The contact Flow is still submittable and stores only contact data | The new `_handle` branch runs after dedup, no-lead and cycle reset, and before `skipped_human`. It accepts only `flow_response` + payload + routing token with the moto prefix **and equal to the current mirror**. It arms the PERDIDO guard and goes to `_process_motorcycle_contact_response`. That handler stores `nombre`/`apellido`/`email`/`canal` only, never overwrites an existing value and never writes `telefono`; it writes no flag or stage. Reproduced: `("Rev", "Iewer Fixture", "rev@example.invalid", "Google", None)`, even though `telefono` was submitted. |
| B.6 | A replay or stale submission cannot reopen automation | The submission consumes the mirror. A replay (same token, new delivery), a stale `moto-handoff-999-1` and a booking-shaped submission are all `skipped_human`, with nothing sent (§3 D6b). Without `needs_human` (only after a cycle reset), a stale moto token goes to the contact handler as before, which only sets `needs_human=True`. That is the safe direction and unchanged against base. |
| B.7 | The review notification is idempotent | The recorded handoff alerts once. Afterwards `needs_human` makes every turn `skipped_human`. The contact handler alerts only on the transition into `needs_human` (`_already_human`). Counted at the real Resend seam (`send_human_review_notification`, with `resend_api_key` and the recipient set): **exactly 1 per thread** in every case (§3). The W1-F2 operator WhatsApp loop is a separate, unchanged, ledger-keyed scanner, not a per-turn CE writer. |
| B.8 | A car thread is unchanged | The predicate is False for a car focus, for an archived MOTO candidate and for a non-moto booking token (§5). Car quote → acceptance → Booking Flow with 1 ISSUED offer, sync and async (§3 D9). |
| B.9 | The kill switch fails closed | The recorded branch reuses the existing plain-text branch: `needs_human` is committed before the send, and `OutboundBlockedError` → `human_handoff_blocked`. A first turn under the kill switch rolls back the token (existing behaviour) but keeps the MOTO candidate, so the candidate half still owns T2. A contact submission under the kill switch returns `blocked_dispatch` with the data stored and no exception (§3 D8b). |
| B.10 | Mock hardening hides no production path | In production, `ctx.candidates` is always `list(...scalars().all())` (`_load_context`, `_reload_active_candidates`, `_execute_cycle_reset`), and `flow_booking_token` is a `String` column. My direct check on loaded production contexts reported `type(ctx.candidates).__name__ == "list"` and the correct truth value in all 4 cases (§5). |

**Code observations (non-blocking, all recorded by the closeout or unreachable):**

- O-1. The `_send_booking_flow` and legacy-issuer gates sit after the caller's earlier writes: with every earlier gate off, the lead is ACEPTADO/SCHEDULING with 0 offers. The closeout records this (residual 4). It is unreachable while Layer A or the acceptance gate stands.
- O-2. With Layer A bypassed on a seeded ACEPTADO/SCHEDULING motorcycle, the scheduling path lists day slots in text ("Para mañana… tengo disponible: 09:00…") with no offer and no Flow. That is availability text, not a commercial step, and it is unreachable because acceptance is gated.
- O-3. A synthetic thread with a pending moto token but **no** MOTO candidate and a car focus gets the existing "Te envié el formulario…" live-mirror copy instead of the handoff, still with 0 offers. No path creates that state: the handoff writes the candidate before the token.

## 3. C/D. Independent reproduction — **PASS**

Probe `$RV/probe/p_rev.py`:

- It is reviewer-written, with **its own** wordings, interpreter script, assertions and counters. It does not import the candidate's test module.
- It runs on the real route through the shared WILD1-C harness seams: models and Meta transport scripted, everything else production.
- The alert is counted at the `resend_email` module seam.
- Every turn checks the following:
  - outbound and Flow text contain none of `$`, `Total`, `cotiza`, `podemos avanzar`, `horarios disponibles`, `Completá el formulario`, `Revisión:`, `Viáticos`;
  - flag is not in {PRESUPUESTO_ENVIADO, ACEPTADO};
  - stage is not in {QUOTED, SCHEDULING};
  - offers = revisions = booked = ledger = 0;
  - no non-moto token.

**C — cross-turn matrix.** 3 vehicles × 6 follow-ups = 18 cases. My wordings were "tengo una moto Honda CB 500", "quiero revisar un cuatriciclo Can-Am Outlander que estoy por comprar" and "hola, es para un UTV Polaris RZR 1000". The follow-ups were:

- "está en Palermo, ¿cuánto sale?"
- "¿cuánto sale?"
- "en Palermo"
- "dale, ¿mañana puede ser?"
- acceptance plus a requested day as the first follow-up: "dale, me sirve, ¿el jueves a la mañana puede ser?"
- a repeated "la moto / el cuatriciclo / el UTV está en Palermo, ¿cuánto sale?"

Each case ran:

- T1 enquiry;
- T2 follow-up;
- T3 "dale, ¿mañana puede ser?";
- T4 contact Flow with the pending token;
- T5 the same submission replayed;
- T6 a price question.

| Turn | Asserted |
|---|---|
| T1 | `flow_button_sent`, contact intro, moto token, `needs_human=False`, 0 alerts |
| T2 | `replied` with exactly `_MOTORCYCLE_HANDOFF_REPLY`, no Flow, `needs_human` and `necesita_humano` both true, **moto token kept**, 1 alert |
| T3 | `skipped_human`, nothing sent |
| T4 | `_MOTORCYCLE_ADVISOR_REPLY`, data stored, token consumed, still human, still 1 alert |
| T5 / T6 | `skipped_human`, nothing sent; exactly 1 MOTO candidate |

**D — other entries.**

| Case | Expectation |
|---|---|
| D1 vehicle-fallback form (Moto, Cuatriciclo, UTV) | T1 contact Flow → T2 handoff → T3 human |
| D2 location fallback after a recorded motorcycle (`needs_human` still false) | handoff reply, token kept, 1 alert |
| D3 website form `Tipo: Moto` | contact Flow → handoff → human |
| D4 website car form after each motorcycle | handoff, no quote |
| D6b stale moto token / booking-shaped submission on a human-owned thread | `skipped_human`, mirror untouched |
| D7 kill switch from T1 (×3) | T1 and T2 `human_handoff_blocked`, `needs_human`, 1 alert, nothing sent; still human after the switch comes back on |
| D8 kill switch only on the follow-up (×3) | `human_handoff_blocked`, token kept |
| D8b kill switch on the contact submission | `blocked_dispatch`, data stored, no exception |
| D9 car control | quote ("Total:", PRESUPUESTO_ENVIADO) → ACEPTADO/SCHEDULING, Booking Flow, offers `["ISSUED"]`, 0 alerts |

**Results.** Every case asserts every check listed above.

| Run | C | D |
|---|---|---|
| Candidate, SQLite, sync | **18/18** | **19/19** |
| Candidate, SQLite, live async (6.0 s worker) | **18/18** | **19/19** |
| Candidate, PostgreSQL 16, sync | **18/18** | **19/19** |
| Candidate, PostgreSQL 16, live async | **18/18** | **19/19** |
| Pre-correction, SQLite, sync / async | 0/18: **6 cases auto-quoted, 3 with an ISSUED offer** | 1/19 (the car control) |
| Pre-correction, PostgreSQL, sync | 0/18: 6 quoted, 3 offers | — |

So the probe reproduces RV-F-01 independently on `pre`, and it is closed on the candidate.

## 4. F. Databases — **PASS**

**SQLite** (`ridecheck-crm-test:l4.7b2`, `--network none`). The new suite: **28 passed, 36 subtests**. I ran 30 focused files, one process per file, on `pre` and on the candidate (`$RV/ev/focused.txt`):

- the 4 changed test files;
- WILD1-C sync, H1 and live async; R3; R4; R4-R1; R4-R2;
- M21.2-MH, M21.2-MF, R5-F1 production path, R5-F1 template contract, WILD04R tipo authority;
- R6 structured contract, L4.7D validator, M20.6B1, f6, m18;
- WILD1 booking-flow gate, forward-only, r3-aligned, SB1, M21.3-C-D, L4.7W4-F1;
- the three kill-switch suites.

My comparator `$RV/ev/cmp.py` gives these results:

| Measure | Result |
|---|---|
| Cases | pre 1,550 / candidate 1,550; 0 added, 0 removed |
| Failing | pre 24 (22 of them the new file's fails-before identities, whose 47 subtest-level failures collapse to 22 tests) / candidate 2 |
| **NEW** | **0** |
| **GONE** | 22, all in the new file |
| **COMMON** | 2, messages unchanged: `test_or_73_no_booking_revision_is_modified` (pre-existing on base, R4 review §9.1) and `TestBR1Row5BareVehicle::test_row5_no_catalog_hit_fuzzy_confirm` (pre-existing; the closeout lists it as `row5`) |

**PostgreSQL 16.13.** Setup (reviewer `pg.sh`):

- disposable `r4r3rev-pg` on tmpfs, `--internal`, no route to live;
- 7 databases, each built as pre-schema record + `alembic stamp 20261006_clarification_attempts` + `upgrade head` → **`20261104_vehicle_identity`** on all 7.

Results:

- New suite + R4 + R4-R1 + R4-R2 + R3 + H1: **244 passed, 109 subtests, 0 failed**.
- The probes are in §3.

**Dialect checks.** The checks count motorcycle threads (non-archived MOTO candidate) per database.

| Database | Motorcycle threads | With an offer | Quoted or accepted | Revisions | Ledger rows | Priced outbound (excl. seeded) | Non-moto token | Duplicate bookings per offer |
|---|---|---|---|---|---|---|---|---|
| candidate C sync / C async / D sync / D async (4 DBs) | 18 each | **0** | **0** | **0** | **0** | **0** | **0** | **0** |
| candidate suite DB | 50 | **0** | 3 | 0 | 0 | **0** | 0 | 0 |
| pre-correction C sync | 18 | **3** | **6** | 0 | 0 | **6** | 3 | 0 |

- The suite DB's 3 quoted/accepted motorcycle threads are the DefenceInDepth fixtures seeded with that flag, as the closeout says. Their engine outbound contains no amount.
- The bypass-fixture DB `pcs_e3` intentionally holds a car quote plus an injected MOTO candidate. It is not a product measurement.

**Teardown** by exact name: 0 `r4r3rev-*` containers, 0 networks. The live `ridecheck-crm-*` containers were untouched (backend up 34 h).

## 5. E. Defence in depth — **PASS**

Bypassing Layer A by "first call" would couple the test to call order. I used two **frame-scoped** bypasses instead (`sys._getframe(1).f_code.co_name`).

**E (Layer A and fallback-form gate off).** 3 seeds, each fed price, acceptance, website car form, SCHEDULING Booking Flow and the legacy issuer (`booking_flow_id=""`):

- (a) candidate only, MOTO focus;
- (b) candidate only, car focus + `mentioned` MOTO;
- (c) token only, car focus + pending moto token.

All 15 cases returned 0 offers, no Flow, no non-moto token and no priced text.

**E3 / E2: each gate in isolation.** The predicate answers truthfully **only** inside the named function and False everywhere else. Each run starts from a real car quote turn, then injects the MOTO candidate (half A) or the moto token (half B), then sends the acceptance.

| Gate alone | Candidate half | Token half |
|---|---|---|
| none (control) | **1 ISSUED offer + Booking Flow**, ACEPTADO | **1 ISSUED offer + Booking Flow** |
| `_compute_price_quote` | no quote | no quote |
| `_handle_quoted_acceptance` | handoff, flag stays PRESUPUESTO_ENVIADO, 0 offers | same |
| `_handle_website_form` | handoff, no quote | same |
| `_send_booking_flow` | handoff, 0 offers, no Flow (ACEPTADO already written by the caller: O-1) | same |
| `_try_schedule_and_flow` (legacy issuer) | handoff on the time turn, 0 offers, no Flow | 0 offers, no Flow (O-3 copy) |

Each gate alone turns the control's offer and Flow into none. Both halves of the predicate drive every gate.

**Negative controls.**

| Thread | Result |
|---|---|
| Car focus + archived MOTO candidate | quoted normally ("Total:", PRESUPUESTO_ENVIADO, no `needs_human`) |
| Plain car | quoted normally |
| Direct predicate on loaded production contexts | car **False**; car + booking token `"<tid>-1794956450-abcd"` **False**; MOTO focus **True**; token only **True** |

## 6. F.5–F.7. Mutation — reviewer-owned, own anchors (`$RV/mut/rev_mutants.py`, `run_mut.sh`)

Method:

- each mutant was applied to a fresh `rsync` copy of the snapshot, with every anchor count asserted to be 1 and the result compiled;
- each ran against the new suite;
- **Control: 28 passed, 36 subtests.**

| # | Mutant | Suite result |
|---|---|---|
| RM01 | **Layer A cross-turn thread guard removed** | **KILLED (34 failing identities)** |
| RM02 | thread guard moved after the current-turn check | KILLED (2: the repeated mention re-sends the Flow) |
| RM03 | predicate ignores `archived` | KILLED (1) |
| RM04 | price gate removed entirely | KILLED (3) |
| RM05 | price gate keeps the MOTO-focus half, loses the thread half | **SURVIVED the suite** |
| RM06 | acceptance gate removed | KILLED (1) |
| RM07 | website-form gate removed | KILLED (1) |
| RM08 | `_send_booking_flow` gate removed | KILLED (1) |
| RM09 | legacy-issuer gate removed | **SURVIVED the suite** |
| RM10 | fallback-form gate removed | KILLED (1) |
| RM11 | `_handle` bypass drops the "equals the mirror" check (replay reopens) | KILLED (18) |
| RM12 | the recorded handoff clears the pending token | KILLED (32) |
| RM13 | contact-response alert not idempotent | KILLED (18) |
| RM14 | a recorded turn goes back to the Flow branch | KILLED (39) |
| RM15 | **RV-F-02 / J10**: the guard restores the pair captured at arm time (keeps `set_committed_value`) | **KILLED (2)**: `test_restore_uses_the_operators_concurrent_reason_not_the_armed_one`, `test_an_operator_reopen_is_not_this_guards_business` |
| RM16 | predicate's token half dead | KILLED (1) |

**Suite: 14/16 killed.** My E2/E3 probe kills both survivors on their mutant trees:

- **RM09:** the legacy path **issues an offer and dispatches a Flow** (offers 1, non-moto token) on a car-focus thread with a recorded MOTO candidate.
- **RM05:** the car focus on a recorded-motorcycle thread is **quoted** (PRESUPUESTO_ENVIADO).

**RV-R3-01 (LOW, test-pin gap; not a defect).** The candidate's suite does not pin two of the second-line gates:

- the legacy data-collection issuer gate;
- the thread half of the price gate.

Both gates are correct and load-bearing, as the probe proves. They sit behind the Layer A thread gate, which RM01 shows is pinned by 34 identities, so production is unaffected. The closeout's own mutant list also has no legacy-issuer mutant.

Classification: optional pin at consolidation, or post-launch debt. It does not block.

**F.7 RV-F-02.** Closed. The database-current re-read is now pinned (RM15 killed by 2 tests), and the production behaviour is unchanged.

## 7. G. Regression boundary — **PASS**

I used the supplied R4 / R4-R1 / R4-R2 evidence; no new architectural review was done. I confirmed only the following:

- **Car quoting and booking remain operational.** D9 passed sync and async, on SQLite and PostgreSQL. CarUnchanged passed both postures. M21.3-C-D, L4.7W4-F1, the WILD1 booking-flow gate, forward-only, r3-aligned, SB1, R6 structured, m18 and f6 all match pre exactly.
- **The motorcycle suites do not regress.** M21.2-MH 18 + 7 subtests, M21.2-MF 31, R5-F1 production path 26, R5-F1 template contract 32 + 272 subtests: identical counts and identities on pre and the candidate. The WILD1-C / R3 / R4 / R4-R1 / R4-R2 suites are identical on SQLite and pass on PostgreSQL (244/244).
- **Motorcycle/quad/UTV never reach automatic commercial automation.** That holds on every entry and follow-up path tested (§3, §4 dialect checks).

**Re-recorded pins.** Each is consistent with the owner contract and documented in its file:

- or_43: 6 → 12 sites, every site still gating-checked;
- s6: now the motorcycle handoff, with no AI call;
- b07 / g08: a mixed car + MOTO thread writes no zone, and nothing leaks to MOTO.

b07/g08 encode closeout residual 1, the human-owned mixed thread. That is an owner-visible consequence of the literal contract, not a defect.

## 8. Residuals carried forward (owner attention, unchanged from the closeout)

1. A mixed thread is human-owned for the rest of the cycle once a motorcycle is recorded.
2. When an operator returns the thread to AI, the next turn hands off again (one alert per hand-back).
3. The advisor acknowledgement is still sent after a text handoff.
4. The second-line gates sit after the callers' earlier writes (O-1).
5. RV-R3-01 (LOW pin gap, §6).
6. RV-F-03 (owner copy) remains untouched.

None blocks.

## 9. Section verdicts

| Section | Verdict |
|---|---|
| A. Isolation and manifest | **PASS** (only production change `conversation_engine.py`; protected scope byte-identical; candidate unchanged) |
| B. Production patch | **PASS** (B.1–B.10) |
| C. Cross-turn reproduction | **PASS** (18/18 × sync/async × SQLite/PG; pre 0/18) |
| D. Other entry paths | **PASS** (19/19 × sync/async × SQLite/PG) |
| E. Defence in depth | **PASS** (each gate load-bearing alone; both predicate halves; negatives clean) |
| F. Databases and mutation | **PASS** (NEW 0; PG 244/244; RM01 killed by 34; RM15 / RV-F-02 killed; 2 suite survivors killed by the probe → RV-R3-01 LOW) |
| G. Regression boundary | **PASS** |

## 10. Verdict and next authorized step

**VERDICT: READY FOR CONTROLLED CONSOLIDATION**

The next authorized step is **controlled consolidation**:

1. Build from the manifest `82f477e0…` (60 paths), not from a directory copy (the worktree holds `__pycache__`), plus the WILD1-C docs.
2. Commit and push.
3. Then the consolidated image/kit update and the dark deployment.

Pinning RV-R3-01 (legacy-issuer gate, price-gate thread half) is optional during consolidation, test-only.

None of those actions was performed by this review: no stage, commit, push, build, kit update, deployment, Meta call, tester contact or booking-11 action, and no WILD window was opened.

STOP
