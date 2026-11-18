PROJECT: RIDECHECK_CRM
TYPE: CLOSEOUT
MILESTONE: PRELAUNCH-MINIMUM-LAUNCH-WILD1-C-R4-R2-BOOKING-PROJECTION-CONSISTENCY

**VERDICT: READY FOR FOCUSED R4 INDEPENDENT REVIEW**

- **Date.** 2026-11-17 (work ran 20:09Z–20:45Z).
- **Worktree / branch.** `/opt/ridecheck-crm-wild1-correction`, `fix/prelaunch-wild1-conversation-correction`.
- **Base.** `aa2c8db08d4e3bf6a1a70acdc48b7d23f4077008` (HEAD unchanged; index empty).
- **Tree state.** The R4-R2 delta is uncommitted, on top of the uncommitted R2/R3/R4/R4-R1 deltas. Nothing was staged, committed, pushed, built or deployed. Meta was not touched. No tester was reset or contacted. No WILD was run. The independent review has not been started.
- **Evidence.** `/opt/ridecheck-builds/crm-wild1-c-r4-r2-20261117T200926Z/` (`$E6`). R4 (`$E4`) and R4-R1 (`$E5` = `/opt/ridecheck-builds/crm-wild1-c-r4-r1-20261117T182550Z/`) evidence are unchanged.

## 0. Inputs read and verified

| Input | sha256 |
|---|---|
| R4 closeout | `08e996ff734300c922c3aaa7bbeeb73d6002e8095cad8e9b7568f3c7a77330fe` |
| R4-R1 closeout | `8ef323ea2a3c692c9487b816899d4fa1298a34c9da96377f55b7f8ed6583992d` (byte-identical to its `$E5/` copy) |
| R4-R1 candidate | worktree `sha256sum -c $E5/cand_manifest.sha256` OK; porcelain identical to `$E5/porcelain_end.txt` |

## 1. Owner rulings (2026-11-17)

1. **Booking after operator PERDIDO.**
   - A valid, successfully persisted Booking Flow submission wins over an earlier operator PERDIDO.
   - The coherent projection is `flag=ACEPTADO`, `motivo_perdida=NULL`.
   - A loss reason never remains on an accepted/booked lead.
   - The projection happens only after BookingAuthority has persisted the booking.
2. **R1-D1, accepted.** An engine-declined lead may receive the quote again with its existing CTA. It stays PERDIDO until an explicit acceptance or an authorized scheduling request applies the existing RECONSIDERED transition. **No change made.**
3. **R1-D3, accepted.** An operator-held PERDIDO turn is terminal for automation: it receives only the manual-review copy, and its FAQ questions are not answered or retained. **No change made.**

## 2. The authoritative projection change (closes R1-D2)

`backend/app/services/booking_authority.py`, `BookingAuthority._apply_canonical_state` is the single point where every successful booking projects its lead (CE Flow response, Data Exchange confirm, and any other caller whose `BookingAttachmentPlan.lead` sets a flag).

```python
for name in ("estado", "flag", "necesita_humano"):
    if getattr(u, name) is not None:
        setattr(lead, name, getattr(u, name))
if u.flag == "ACEPTADO":
    # WILD1-C-R4-R2 (owner ruling 2026-11-17) ...
    lead.motivo_perdida = None
```

`_REQUIRED_COLUMNS_LISTED["leads"]` also gains `"motivo_perdida"`, so the per-booking PostgreSQL schema preflight (`verify_booking_schema`) covers every column the authority now writes.

**Why this is "only after persistence".** In `execute()` the order inside the one booking transaction is:
1. K1/K2 locks;
2. admission and claim;
3. calendar/overlap checks under K3–K6 and availability revalidation;
4. `_write` (the Revision);
5. `_apply_attachments` (K7, the ThreadRevision);
6. `_apply_canonical_state` (K8 lead lock, then **this projection**, then the K1/K2 thread and offer projection);
7. `_record` (K9 ledger);
8. `_verify_before_commit` / `_verify_offer_projection`;
9. `commit()`.

So the clear is written only after the booking rows exist in the same transaction. Every refusal returns before step 6. Every exception after step 6 runs `_safe_rollback()`, which reverts the clear together with the booking. Proven three ways:
- the spy test (§5): at the projection point `revision.id` and `tr_id` exist and the lead is still `PERDIDO/PRECIO`; right after it, `ACEPTADO/None`;
- the forced-exception and rollback tests: the motivo survives;
- mutant R2-02 (clearing before `_write`) is killed.

**Not done, by design:** no ConversationEngine cleanup after BookingAuthority returns, no migration, and no change to admission, slot selection, receipt wording, offer lifecycle or the Data Exchange service.

## 3. Manifest

R4-R2 delta against the R4-R1 candidate (`$E5/cand`): **5 paths**, one of them production.

| Path | Δ vs R4-R1 | sha256 | git blob |
|---|---|---|---|
| `backend/app/services/booking_authority.py` | +8 / −1 (first change to this file against base) | `f1580e80c9473a442e9c83116ed2e0f664364235d63856c9b882ea46008dd9f8` | `0e6cc6b03eefb81f27e9fb01dd2ae260ca06f3df` |
| `tests/test_wild1_c_r4_r2_booking_projection.py` | new (321 lines, 15 tests) | `a252d0f78de4756db360128ea11b17f12bde22c2dc4bc512fd3f47d4cda797be` | `d2e8b1ff8ffca59033db0f30ef8113be5070f599` |
| `tests/test_sb1_booking_representation.py` ⚑ | +6 / −1 | `4d7d5837a8cb38877a0bd4aba2597212f01a163907bfae5e141daced4b94ba89` | `a66123d6820427a1bf187406c212b36df4bcc7db` |
| `tests/test_l4_7w5_g3_7f_owner_resolution.py` ⚑ | +4 / −1 | `544623b982d5cb5abdaca532f3f026fdd727a1d738ec8733359966044fb65bee` | `3fa7501c3110c4b9a021a4eaeca32828e1259d10` |
| `tests/test_l4_7w5_g3_6_proposition_migration.py` ⚑ | +7 / −2 | `c9308e3a825b29aa6ec6cad1d08260ccd7da0f0e3542ae15f615135374e8025f` | `2874770306b9de13d5a95d9fff4da82d9079f6b0` |

The full candidate is 57 paths against base. `$E6/cand/` is a full-repository copy, byte-identical on all 57: **`sha256($E6/cand_manifest.sha256)` = `1f71403028203ab2f89a0b894efd52aadad8ae1a3e1143c77c2f7422e2f665b3`**.

`conversation_engine.py`, `semantic_interpreter.py`, `claim_coverage.py` and `semantic_turn_evidence.py` are byte-identical to R4-R1.

### ⚑ Three pre-existing pins that R4 / R4-R1 had broken, found here and re-recorded (test-only)

The narrow booking set and a bounded follow-up sweep (§6.3) ran suites that **R4's and R4-R1's focused and extra sets did not include**. Three tests failed on the R4-R1 tree and passed on base. None is a behavioural regression; each pins a deliberate R4 or R4-R1 change.

| Test | What broke it | Re-recording |
|---|---|---|
| `test_sb1_booking_representation::test_sb1_20_frozen_files_unchanged` | R4's `TurnSemanticEvidence.dispatched` property changed the frozen blob of `semantic_turn_evidence.py` | Blob re-recorded (`50118bf6…` → `28968584…`), with the established justification comment naming WILD1-C-R4 |
| `test_l4_7w5_g3_7f_owner_resolution::test_or_43_only_proven_moto_callers_reach_the_motorcycle_helper` | R4-R1 added a 6th `_motorcycle_human_handoff` caller (`_hold_perdido_fallback`). It passes the test's own gating check (`tipo == "MOTO"` / `_is_motorcycle_enquiry`); only the count of 5 failed. | Count 5 → 6, with a comment |
| `test_l4_7w5_g3_6_proposition_migration::test_g36_48_…` | The same intended R4 change as `bc36_34` / `g35e_24` (R4 §7.4): `evidence=None` is a failed reading, so the burst is retained and acknowledged | Expected 2 → 3 items, plus an assert that the ack is in the one message |

**Correction to the R4 and R4-R1 closeouts.**
- Their "NEW 0" claims hold for the sets they ran, but those sets were narrower than described.
- R4 §7.3 described its 19-file "semantic extra set" as including the suites "that use the failing-reading harnesses". The final selection pattern dropped the `evidence=None` harness users that its first version matched (`test_l4_7w5_g3_6_proposition_migration.py`, `test_l4_7w5_g3_5c_canonical_record_fidelity.py`, `test_m21_1_1_service_intent_gate.py`), and it did not include the SB1 or G3-7F owner-resolution suites.
- All of those have now been run (§6.3), with NEW 0 against base.

## 4. Before / after state table (real routes)

| Scenario | R4-R1 (before) | **R4-R2 (after)** |
|---|---|---|
| operator PERDIDO/PRECIO + valid booking (CE) | booked; `ACEPTADO/PRECIO` | booked once; **`ACEPTADO/None`**; offer ACCEPTED; one receipt after persistence |
| operator PERDIDO/OTRO + valid booking (CE) | `ACEPTADO/OTRO` | **`ACEPTADO/None`** |
| engine decline PERDIDO/PRECIO + valid booking | `ACEPTADO/PRECIO` | **`ACEPTADO/None`**; decision history unchanged (`[DECLINED/ENGINE]`, append-only) |
| normal booking (flag ACEPTADO, no motivo) | `ACEPTADO/None` | `ACEPTADO/None` (unchanged) |
| operator PERDIDO/PRECIO + Data Exchange confirm | `ACEPTADO/PRECIO` | **`ACEPTADO/None`**, identical to the CE projection (flag, motivo, estado, necesita_humano) |
| invalid token / expired offer / withdrawn offer | PERDIDO + motivo, 0 bookings | same |
| occupied slot (CE and DX) | PERDIDO + motivo, 0 bookings | same |
| forced exception in the ledger write / rollback at final verification (CE), rollback (DX) | PERDIDO + motivo, 0 bookings, 0 ledger rows | same |
| duplicate delivery / same payload new id / different payload / DX after CE | `ACEPTADO/PRECIO` kept | `ACEPTADO/None` kept; 1 booking, 1 ledger row, no second receipt |

The R4-R2 suite on the R4-R1 code (`$E6/runs/before2.xml`): **8 fail, 7 pass**. That is every success and idempotency check of the motivo, the transaction spy, the CE/DX equivalence and the catalogue. The 7 that pass are the failure paths and the normal booking. On R4-R2: **15 / 15 pass**.

## 5. Tests (`tests/test_wild1_c_r4_r2_booking_projection.py`)

| Prompt item | Test |
|---|---|
| A (operator PRECIO), "only after persistence", receipt timing | `test_a_operator_perdido_precio_cleared_only_inside_the_booking_transaction`. Spy on `_apply_canonical_state`: before = `(revision written, ThreadRevision written, PERDIDO, PRECIO)`, after = `(ACEPTADO, None)`. A transport hook reads a separate session **when the receipt is sent** and already sees 1 booking, 1 ledger row, ACCEPTED, `ACEPTADO/None`. |
| B (operator OTRO) | `test_b_operator_perdido_otro` |
| C (engine decline) | `test_c_engine_declined_perdido_history_stays_append_only` |
| D (normal) | `test_d_normal_booking_is_unchanged` |
| CE ≡ DX | `test_data_exchange_projects_the_same_state` |
| E | `test_invalid_token`, `test_expired_offer` (`expires_at` in the past), `test_withdrawn_offer`, `test_occupied_slot` (CE and DX on a slot another booking holds), `test_forced_persistence_exception_inside_the_transaction` (`_record` raises), `test_transaction_rollback_at_the_final_verification` (`_verify_before_commit` raises `_InvariantBreach`), `test_data_exchange_rollback` |
| F | `test_duplicate_same_payload_and_different_payload`, `test_data_exchange_duplicate_after_ce_booking` |
| preflight | `test_the_projected_column_is_in_the_booking_schema_preflight` |

**Slots.** Every successful booking gets a unique (day, time) from a module-level allocator inside the 14-day horizon and business hours. It avoids the H1 and R4-R1 booking days, so the one launch professional is never contended, on SQLite or on a shared PostgreSQL database. A first PostgreSQL run without the allocator failed 5 tests on `OVERLAP` / slot conflicts between suites (`$E6/pg/pg_r4r2_v3_slotclash.log`).

## 6. Results

### 6.1 SQLite (runner `ridecheck-crm-test:l4.7b2`, `--network none`, read-only mounts)

| Set | base `aa2c8db` | R4-R1 | **R4-R2** |
|---|---|---|---|
| Narrow booking set (26 files, `$E6/booking_set.txt`): SB1, SB2A (5), convergence P2/P3/P3-R1/P4/P4-R1/strict schema (2), Flow Data Exchange, M21.3-C-D Flow, WILD1 booking-flow gate, minimum launch (3), H1 booking regression, motorcycle handoff sentinels (2), R4-R1, R4-R2 | 537 / 0 failing (23 files present) | 566 / **1 failing** (`sb1_20`) | **582 / 0 failing** |
| — comparison | | | base → R4-R2: NEW 0 / GONE 0; R4-R1 → R4-R2: NEW 0, GONE 1 (`sb1_20` fixed) |
| R4-R2 + R4-R1 suites | | | 32 / 32 |

### 6.2 PostgreSQL 16 (disposable `w1cr4r2-pg`, tmpfs, `--internal`, schema via pre-schema record + stamp + `upgrade head` → `20261104_vehicle_identity`)

**45 passed / 0 failed**: R4-R2 15, R4-R1 17, H1 regression 13. This covers the operator-PERDIDO success, normal success, both rollback cases and every duplicate case, on real separate connections, through BookingAuthority's PostgreSQL schema preflight (now including `motivo_perdida`). Duplicate `(thread_id, claim_id)` is 0. Torn down by exact name; 0 `w1cr4r2-*` left.

The SB2A/convergence `_pg` suites need several purpose-built databases (`SB2A_PG_URL`, `SB2A_PG_MIG_URL`, `SB2A_PG_STAMP_URL`, `CONV_P3_PG_URL`) and were not run, per the narrow test budget.

### 6.3 Bounded pin sweep (test-only re-recordings, §3 ⚑)

| Set | base | R4-R2 |
|---|---|---|
| 10 suites that hash or pin engine sources (`$E6/pin_set.txt`) | 463 passed / 1 failing (`or_73`, pre-existing) | NEW 0 / GONE 0 |
| 42 further suites reading engine/booking-authority source text, not in any earlier set (`$E6/pin_extra_set.txt`) | 1667 / 1 failing (same `or_73`) | NEW 0 / GONE 0 |
| The 2 `evidence=None` harness suites R4's extra set missed, plus the 3 re-recorded files | 377 / 3 failing (pre-existing: `or_73`, `si_h1`, `si05`) | NEW 0 / GONE 0 |

The complete historical matrix was not re-run (test budget).

## 7. Final-tree mutation results

Method: same as R4.
- Each mutant is exact-string hunks, each applied once to a throwaway copy of `$E6/cand`, then compiled.
- Test group: R4-R2, R4-R1, Flow Data Exchange, convergence P3 cutover, P3-R1 ack, SB2A booking authority, SB2A-R3 typed plans and H1.
- KILLED means a failing test identity absent from the control.
- Control: 167 tests, 0 failing.

**6 / 6 KILLED** (`$E6/mut/results.txt`).

| ID | Mutant (prompt item) | Killed by (one of) |
|---|---|---|
| R2-01 | ACEPTADO projected, motivo not cleared (H.1) | 6, e.g. `test_a_…`, `test_duplicate_same_payload_and_different_payload` |
| R2-02 | motivo cleared **before** `_write` (H.2) | `test_a_…cleared_only_inside_the_booking_transaction`, `test_occupied_slot` |
| R2-03 | the CE refusal handoff clears motivo (H.3) | `test_forced_persistence_exception_inside_the_transaction`, `test_occupied_slot`, `test_transaction_rollback_at_the_final_verification` |
| R2-04 | duplicate delivery skips the ledger replay (H.4) | `test_p3u_20`, `test_p3u_21`, `test_p3u_42` (convergence). The R4-R2 idempotency test still holds under this mutant, because the CE refusal path re-reads the ledger and replays without a second booking or receipt (B-6). |
| R2-05 | CE and DX project differently (H.5): clearing skipped for `CONVERSATION_ENGINE` | 6, incl. `test_data_exchange_projects_the_same_state` |
| R2-06 | `motivo_perdida` missing from the booking preflight | `test_the_projected_column_is_in_the_booking_schema_preflight` |

## 8. Protected-path proof and live check

- **Unchanged against base.** `git diff --stat aa2c8db` is empty over:
  - `backend/alembic`, `models.py`, `settings.py`, `routes/`, `repositories/`;
  - `pricing.py`, `outbound_safety_gate.py`, `offer_lifecycle.py`, `flow_contract.py`;
  - `booking_flow_service.py`, `booking_ce_adapter.py`, `booking_acknowledgement.py` (receipt wording);
  - `meta/`, `data/`, `docker-compose*.yml`.
- **Changed.** `booking_authority.py` alone: +8/−1.
- **Unchanged against R4-R1.** The conversation engine (interpretation, retention, semantic deadline, PERDIDO hold), `semantic_interpreter.py` (model/prompt) and operator provenance (`kanban_actions.py`, `api/leads.py`, `quote_decision_history.py`) are byte-identical. No migration.
- **Live read at 2026-11-17T20:37:52Z.** The backend image is `sha256:4952d5d5…`, `GIT_SHA=aa2c8db…`, `OUTBOUND_ENABLED=false`, `SMART_BOOKING_CE_ENABLED=false`; unchanged.

## 9. Remaining debt

| Item | State |
|---|---|
| R1-D1, R1-D3 | accepted by the owner (§1); unchanged |
| R1-D2 | **closed** |
| R1-D4 and R4 §12 items (R4-D3…D6, carried R3RV-05 / WC-05 / WC-06) | unchanged |
| `test_l4_7w5_g3_7f_owner_resolution::test_or_73…` subtest, `test_m21_1_1…::test_si_h1…` and `::test_si05…`, `test_caseC_requested_day` | pre-existing on base; untouched |
| SB2A/convergence `_pg` certification suites | not run in this revision (they need dedicated databases) |

## 10. Exact next authorized step

The next step is the **combined focused R4 independent review**, covering R4 + R4-R1 + R4-R2. It should use:
- the candidate `$E6/cand` (manifest sha256 `1f714030…f665b3`);
- the R4, R4-R1 and R4-R2 closeouts;
- the mutation runners `$E4/mut`, `$E5/mut` and `$E6/mut`;
- the PostgreSQL scripts `$E4/pg/pg_r4.sh`, `$E5/pg/pg_r4r1.sh` and `$E6/pg/pg_r4r2.sh`;
- the probes `$E4/probes` and `$E5/probes`.

It should look specifically at:
- the `BookingAuthority` projection change and its transaction placement (§2);
- the three re-recorded pins (§3 ⚑);
- the corrected scope of the R4 extra set.

No commit, push, build, kit update, deployment, Meta change, tester contact or WILD comes before that review's verdict.

**VERDICT: READY FOR FOCUSED R4 INDEPENDENT REVIEW**
