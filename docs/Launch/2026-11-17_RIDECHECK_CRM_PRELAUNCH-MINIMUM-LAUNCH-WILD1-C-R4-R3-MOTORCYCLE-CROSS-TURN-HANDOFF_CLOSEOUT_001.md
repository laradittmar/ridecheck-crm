PROJECT: RIDECHECK_CRM
TYPE: CLOSEOUT
MILESTONE: PRELAUNCH-MINIMUM-LAUNCH-WILD1-C-R4-R3-MOTORCYCLE-CROSS-TURN-HANDOFF

# WILD1-C-R4-R3 — motorcycle / quad / UTV cross-turn human handoff (RV-F-01)

- **Date.** 2026-11-17 / 18 (UTC).
- **Gate.** L4 runtime certification (ACTIVE). Scope is limited to RV-F-01, as §14 of the R4 final focused review authorizes (`…WILD1-C-R4-FINAL-FOCUSED-INDEPENDENT-REVIEW_AUDIT_001.md`). RV-F-02 rides along as a test-only pin.
- **Candidate.** Uncommitted tree on base `aa2c8db`, branch `fix/prelaunch-wild1-conversation-correction`. The index is empty.
- **Pre-correction tree.** The reviewed R4-R2 candidate, manifest `1f714030…`. It was verified with `sha256sum -c` before the first edit and snapshotted.
- **Evidence.** `$E7=/opt/ridecheck-builds/crm-wild1-c-r4-r3-20261118T003912Z/`. It holds `pre/` (the snapshot), `ev/` (runners, junit, logs, comparator, PostgreSQL), `probe/` (three-turn probe and transcripts) and `mut/` (mutants, results).
- **New manifest.** `$E7/cand_manifest.sha256` (60 `.py` paths) has sha256 `82f477e095fcddd9af0c09e270b2143b354e5a6dca239fdc8ae54d59bac1c2c0`.

Nothing was staged, committed, pushed, built or deployed. No kit was updated. Meta and the testers were not contacted, booking 11 was not touched, and no WILD window was opened. Every test container ran with `--network none`, or on an `--internal` network for PostgreSQL. The live containers were never touched.

## 1. Root cause

`ConversationEngine._motorcycle_human_handoff` handles the first enquiry by sending the contact-data Flow (live has `WHATSAPP_FLOW_ID`). On that branch it does three things:

- records a `mentioned` MOTO candidate;
- mints a `moto-handoff-` token;
- deliberately leaves `needs_human=False` until the form is submitted.

Layer A in `_process_text`, the only motorcycle gate on the text path, read **only the current turn** (`_is_motorcycle_enquiry(current_turn_text)`). A later turn without a motorcycle word therefore went down the ordinary path, with the MOTO candidate as the focus:

- `_compute_price_quote` priced `MOTO,120000`;
- the quote moved the lead to PRESUPUESTO_ENVIADO / QUOTED;
- the next acceptance moved it to ACEPTADO / SCHEDULING;
- `_send_booking_flow` issued an offer, which superseded the moto token on the mirror, and dispatched the Booking Flow.

The thread already persisted enough to know it was a motorcycle. No code read it.

## 2. Change (production: `backend/app/services/conversation_engine.py` only, +112/−5)

| Function | Change |
|---|---|
| `_thread_motorcycle_recorded` (new) | Thread-level authority built from what the first enquiry already persists. It returns True for a MOTO-typed candidate (`_MOTO_TIPO_VALUES`, not `archived`) in this cycle's `ctx.candidates`, or for a pending `moto-handoff-` token on the mirror. Only real `str` values count. No prose, no model output, no migration. |
| `_process_text` Layer A | The **thread** check runs first and returns `_motorcycle_human_handoff(recorded=True)`. The current-turn check is unchanged after it. |
| `_motorcycle_human_handoff` | New `recorded` argument, decided by the caller. It takes the existing plain-text branch (`needs_human`, `necesita_humano`, the existing review notification, `_MOTORCYCLE_HANDOFF_REPLY`). It records no second candidate and sends no second Flow, and it leaves the pending contact-Flow token in place. A first identification, including the G3-7F-R2 correction-to-MOTO path, still gets the contact-data Flow. |
| `_handle` | On a `needs_human` thread, a Flow response whose routing token **equals the pending moto mirror** goes to `_process_motorcycle_contact_response`, so the contact data is stored. Every other delivery, including a replay after the token is consumed, stays `skipped_human`. |
| `_process_motorcycle_contact_response` | The review notification fires only on the transition into `needs_human`, so it is idempotent when a text turn already handed off. |
| `_process_flow_response` | A vehicle or location fallback form on a recorded-motorcycle thread goes to the handoff. It runs before the PERDIDO hold and never writes flag or motivo. |
| Defence in depth | Gates before each commercial step: `_compute_price_quote` returns None for a MOTO focus or a recorded thread; `_handle_quoted_acceptance`, `_handle_website_form`, the legacy data-collection offer issuer and `_send_booking_flow` each hand off before any write (the last two after the existing `booking_dispatch_allowed` boundary). |

There is no new copy, pricing, authority, Flow type, migration, setting or path id. `outbound_safety_gate`, `offer_lifecycle`, `booking_authority`, `pricing`, `models` and `settings` are unchanged. The kill switch is unchanged: the plain-text branch keeps its existing `OutboundBlockedError` handling.

## 3. Tests

**New file:** `tests/test_wild1_c_r4_r3_motorcycle_cross_turn.py`, 28 tests and 36 subtests, on the real `/api/conversation/handle` route.

- **CrossTurnHandoff** runs sync and again in the live async posture. For moto, cuatriciclo and UTV it covers T1 → contact Flow, then each of the three follow-ups, then "dale, ¿mañana puede ser?". After that come the contact Flow submission (data stored, token consumed, still human), a replayed submission (`skipped_human`, nothing sent) and a further price turn. It asserts:
  - no price, `$`, CTA or Flow;
  - no PRESUPUESTO_ENVIADO or ACEPTADO, no QUOTED or SCHEDULING;
  - 0 offers, revisions, bookings and ledger rows, and no booking token;
  - exactly 1 review notification;
  - exactly 1 MOTO candidate.

  It also covers accept-plus-day as the first follow-up, a repeated "la moto…" (no second Flow), and the direct submission (pre-existing behaviour).
- **OtherEntries** covers:
  - a vehicle-fallback form with MOTO, Cuatriciclo or UTV;
  - a car vehicle/location fallback after a motorcycle;
  - a website form with `Tipo: Moto`;
  - a website car form after a motorcycle.
- **KillSwitchOff** turns `OUTBOUND_ENABLED=false` on for all turns, then for the follow-up only. Results: `human_handoff_blocked`, `needs_human` kept, nothing commercial.
- **DefenceInDepth** bypasses the Layer A thread check and shows that price, acceptance, website form and `_send_booking_flow` each still refuse. It pins the predicate directly (token only, candidate only, `motocicleta`, an archived candidate, a booking token, a car).
- **CarUnchanged** runs sync and live async: a car is quoted (`Total:`, PRESUPUESTO_ENVIADO), then accepted into the Booking Flow (ACEPTADO / SCHEDULING, 1 ISSUED offer, no `needs_human`, 0 notifications).
- **PerdidoGuardReadsTheDatabase** (RV-F-02, test-only) pins two things. In-session, after the flush, the restored pair is the operator's concurrent `DISPONIBILIDAD`, not the armed `PRECIO`. And an operator reopen lets the engine write through.

**Fails before / passes after (same final test file):**

| Tree | Result |
|---|---|
| pre-correction | **47 failed / 17 passed** (identities, subtests included) |
| corrected | **28 passed, 36 subtests passed** |

The 6 tests that pass on pre are the intended controls:

- direct contact-Flow submission ×2;
- car ×2;
- RV-F-02 pins ×2. These are test-only; the production behaviour was already correct (audit §5).

**Re-recorded pins** (each is documented in its file):

| Pin | Change |
|---|---|
| `test_l4_7w5_g3_7f_owner_resolution::test_or_43` | Call sites 6 → 12. `_thread_motorcycle_recorded(ctx, state)` is accepted as a proven-MOTO gate; the per-site gating check still runs on all 12. |
| `test_br1_intent_gate::test_s6_moto_candidate_not_provenance` | A thread with a MOTO candidate now gets the motorcycle handoff (no AI call, `needs_human`) instead of the BR-1 uncertain-service question. It is still never provenance. |
| `test_w1f_w01_r1_evidence_admission::test_b07` and `test_g08` (isolation part) | A car focus plus a `mentioned` MOTO is now human-owned, so no zone is written to either candidate (it used to zone the car). The no-leak-to-MOTO assertion is unchanged. |

**Mock hardening.** The first affected run showed 5 NEW failures in `f6` (3), `m18` and `wild1_booking_flow_gate`. They came from engines built with `__new__` or mock state, where a `MagicMock` token counted as truthy. The predicate now accepts only real `str` / `list` values, and all 5 pass.

## 4. Before / after three-turn reproductions (reviewer-style probe `$E7/probe/p_moto3.py`, real route)

The pattern is identical for moto, cuatriciclo and UTV, sync and live async:

| T2 | Pre-correction T2 → T3 | Corrected T2 → T3 |
|---|---|---|
| "está en Palermo, ¿cuánto sale?" | **"Genial! Te paso la cotización…"**, PRESUPUESTO_ENVIADO / QUOTED → **ACEPTADO / SCHEDULING, Booking Flow "Para mañana miércoles 18/11 tengo 5 horarios…", 1 ISSUED offer** | `_MOTORCYCLE_HANDOFF_REPLY`, `needs_human`, moto token kept → `skipped_human` |
| "¿cuánto sale?" | generic handoff (`needs_human`), but the moto token is **cleared**, so a later contact form would be lost → `skipped_human` | handoff, token kept → `skipped_human` |
| "en Palermo" | generic intro, no handoff → **T3 auto-quotes** (PRESUPUESTO_ENVIADO / QUOTED) | handoff → `skipped_human` |

On every T1, both trees send the contact-data Flow ("Perfecto, para avanzar necesito que completes estos datos.") with a `moto-handoff-` token and `needs_human=False`. That behaviour is preserved.

## 5. SQLite and PostgreSQL

| Run | Result |
|---|---|
| New suite, SQLite | 28 / 28 |
| Affected suites, SQLite | 130 files (the reviewer's 36 focused files ∪ every suite that names a changed function or pins engine source), one pytest process per file. **pre 5,458 cases / 10 failing; corrected 5,458 / 10 failing; NEW 0, GONE 0, COMMON 10, messages unchanged, 0 added / 0 removed.** The COMMON failures are pre-existing: `or_73`, `row5`, `si_h1`, `si05`, `i13`, `obs_04` ×2, `rc10` ×2 and the `smoke_f4_postgres` collection error. |
| PostgreSQL 16 | Disposable, tmpfs, `--internal`; head `20261104_vehicle_identity` on 4 databases. New suite + R4 + R4-R1 + R4-R2 + R3 + H1: **244 passed, 109 subtests, 0 failed.** |
| PostgreSQL probe (9 cases each) | Corrected sync: 0 quoted, 0 offers, 9/9 T2 motorcycle handoff. Corrected live async: the same. Pre-correction sync: **6 quoted, 3 offers, 0/9 handoff**. |
| PostgreSQL dialect checks | Probe DBs on the corrected tree: MOTO threads with an offer **0**, quoted or accepted **0**, booked **0**, priced outbound **0**. Pre-correction: 3 offers, 6 quoted, 6 priced outbound. Test DB: 0 offers and 0 bookings on MOTO threads. The 3 flagged MOTO threads are DefenceInDepth fixtures seeded with that flag; their single engine outbound is "Antes de avanzar necesito confirmar el precio…", which has no amount. The single "Total:" row is a seeded (`SEED`) fixture outbound. Duplicate bookings per offer: 0. Teardown by exact name left 0 containers and 0 networks. |

## 6. Mutation (`$E7/mut/`; a fresh copy per mutant against the new suite)

**12 / 12 KILLED.**

| Mutant | Failing identities |
|---|---|
| **M01 — remove the Layer A cross-turn guard** | **34** |
| M02 — no `needs_human` contact-Flow bypass | 18 |
| M04 — contact response notifies every time | 18 |
| M05 — no price defence | 3 |
| M06 — no acceptance defence | 1 |
| M07 — no Booking Flow defence | 2 |
| M08 — no website-form defence | 1 |
| M09 — no fallback-form gate | 1 |
| M10 — candidate half of the predicate dead | 8 |
| M11 — token half of the predicate dead | 1 |
| M12 — a recorded follow-up re-sends the contact Flow | 39 |
| J10 (RV-F-02) — guard restores the armed pair | 1 |

One mutant (an "already human" guard in the plain-text branch) survived because that code is unreachable: every `needs_human` turn exits before routing. The hunk was removed from production rather than kept.

## 7. Residual limitations (owner attention)

1. **Mixed threads.** A thread that recorded a motorcycle is human-owned for the rest of the cycle, including a later car conversation on the same thread. This is the literal owner contract ("identified anywhere on a thread"), but it supersedes the M21.2.14 design pinned by b07/g08. Automation resumes only after a cycle reset.
2. **Operator return to AI.** If an operator clears `needs_human` on a motorcycle thread, the next customer turn hands off again (one new review notification per hand-back). There is no "motorcycle resolved" state; adding one would be new authority, which is out of scope.
3. **Advisor acknowledgement.** A contact form submitted after a text handoff still gets the existing `_MOTORCYCLE_ADVISOR_REPLY` on a human-owned thread. It is the approved form acknowledgement and carries no commercial claim.
4. **Defence-gate ordering.** The `_send_booking_flow` and legacy-issuer gates sit after the caller's own earlier writes. They are second-line gates: the Layer A thread gate and the price and acceptance gates precede every such caller.
5. **Provenance boundary.** The authority is cycle-scoped through the existing candidate watermark. A MOTO candidate created by a path that never called the handoff (none known) would still be gated, and would also be refused a price.
6. **Not rerun.** The historical full suite was not rerun, and the RV-F-03 copy is untouched.

## 8. Recommended delta-review scope

- Read `conversation_engine.py` against `$E7/pre/backend/app/services/conversation_engine.py`: the 112+/5− delta in the 9 functions of §2.
- Review the new test file and the four re-recorded pins (or_43, s6, b07, g08).
- Reproduce the §4 probe sync, live async and on PostgreSQL, extending it to the vehicle-fallback, website-form, kill-switch and replayed-submission entries.
- Run an independent mutant of the Layer A thread gate.
- Check that the car path and the M21.2-MF/MH and R5-F1 motorcycle suites are identical.
- R4, R4-R1 and R4-R2 need regression evidence only, which is supplied in §5.

STOP
