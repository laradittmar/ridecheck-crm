PROJECT: RIDECHECK_CRM
TYPE: CLOSEOUT
MILESTONE: L4.7C.3B-ACCEPTANCE-AUTHORITY-CUTOVER

# L4.7C.3B — acceptance decided by a predicate, not by a word

Date: 2026-09-03
Acceptance and commercial progression only · flag-guarded and reversible · crm_test only ·
C2 vehicle/location authority still ON · OUTBOUND OFF · production untouched · no scheduling
or booking authority moved.

---

## 1. Verdict

**PASS.** A language match no longer advances commercial state. With
`RECONCILER_ACCEPTANCE_AUTHORITY_ENABLED` on, the predicate proven in C3A decides and records
why; with it off, the legacy branch is reached unchanged. Every safety metric is zero and
coverage rose to **95.2 %**.

## 2. Part 1 — the unaccented "si", fixed as a grammatical invariant

The rule is not a sentence list. **A conditional needs a consequence**: `si` introduces a
protasis only when something follows that could be the apodosis. An accented `sí` is never a
condition; an unaccented `si` followed by fewer than three words is an affirmation.

| Turn | Temporality | Modality | Reading |
|---|---|---|---|
| `Si avancemos` | PRESENT | FACTUAL | affirmation |
| `si dale` · `si coordinemos` | PRESENT | FACTUAL | affirmation |
| `Sí, avancemos` | PRESENT | FACTUAL | affirmation |
| `si me cierra te hablo` | FUTURE | CONDITIONAL | condition |
| `si puedo te aviso` | FUTURE | CONDITIONAL | condition |
| `si consigo el auto avanzamos` | PRESENT | CONDITIONAL | condition |

The heuristic is deliberately conservative — a long si-clause reads as conditional, which
*withholds* authorization rather than granting it — and it is documented as an approximation
of the grammar, not as a fact about it. A test parses the module, strips docstrings and
comments, and fails if any of these phrases appears in executable code.

**Effect: valid acceptance coverage 19/21 → 20/21 = 95.2 %**, with no conditional case
regressing.

## 3. The defect the live probe caught

The first cutover build wired the gate but built the ACCEPT claim **unconditionally** — the
caller had matched acceptance language, so the gate assumed a stance. That would have let the
weak `_has_acceptance_word` guard pass *"Hola, ¿cómo están? **Bueno**, quería revisar una
2008…"* straight through the predicate: the exact false positive this milestone exists to
close, reintroduced one layer higher.

The fix is structural, not a phrase block: **the stance is evidence, not an assumption of the
caller.** A claim is created only when the turn is acceptance *throughout* (`_is_acceptance`);
a single acceptance-shaped word inside a longer sentence carries no stance, so the predicate
has nothing to authorise and the turn holds.

```
Bueno, quería revisar una 2008 del 2014   quote sent   HOLD   no acceptance evidence in this turn
```

Residual, stated plainly: a message consisting of nothing but "Bueno" still matches the strict
legacy matcher and would authorise against a delivered current quote. In Argentine Spanish a
bare "Bueno" after a quote is plausibly agreement, so this is left as correct behaviour rather
than patched away.

## 4. The single acceptance write path (Part 3)

Four QUOTED progression sites are now behind authorization:

| Site | Gate |
|---|---|
| `_is_acceptance` → `_handle_quoted_acceptance` | `authorize_quote_acceptance` must ALLOW |
| the weaker `_has_acceptance_word` guard on the AI branch | same predicate, and it now needs a real stance |
| ordered scheduling proposal in QUOTED (`≥2 branches`) | `authorize_scheduling_progression` |
| day+time and day-only proposals in QUOTED | `authorize_scheduling_progression` |

`authorize_scheduling_progression` shares the deterministic prerequisites — delivered,
current, same-cycle quote — but requires **no stance**, because its trigger is a parsed
day/time. That is the boundary Part 10 draws: a day proposal can no longer advance on a
stale, undelivered or previous-cycle quote, and **scheduling interpretation is untouched**
(`_parse_scheduling_text` and `_parse_scheduling_requests` are unchanged; that is C4).

Legacy helpers keep producing evidence. Neither `_is_acceptance` nor `_has_acceptance_word`
can advance canonical state on its own while the flag is on.

## 5. The predicate (Part 4) — unweakened

```
ALLOW ⟺ stance ACCEPT (read, not derived) ∧ ASSERTED ∧ PRESENT ∧ FACTUAL
      ∧ quote exists ∧ quote DELIVERED ∧ same cycle ∧ inputs unchanged
      ∧ ¬SEARCHING_NOT_READY ∧ ¬candidate conflict ∧ ¬location conflict
```

Confidence appears in no branch. Quote identity is unchanged from C3A —
`sha256(cycle · revision · candidate · category · zone · amount)[:16]`, derived, **no
migration**. Delivery proof is unchanged: an amount present in a current-cycle outbound
`whatsapp_messages` row; `lead.flag` and `stage` corroborate but never prove.

## 6. Safety metrics (Part 18 gate)

| Metric | Result |
|---|---|
| false progression | **0** |
| stale-quote progression | **0** |
| prior-cycle progression | **0** |
| computed-but-not-delivered progression | **0** |
| unsupported authorization | **0** |
| single acceptance write path | **YES** |
| LEGACY_SAFER | **0** |
| UNEXPLAINED_DISAGREEMENT | **0** |
| valid explicit acceptance coverage | **20/21 = 95.2 %** (≥ 90 % required) |
| WILD-A-03 "Si avancemos" | **PASS** |
| "Bueno, quería revisar una 2008…" | **SAFE** |

The single remaining coverage miss is **SYN-ACC-14** *"Ok, cuándo pueden?"*. Audited per
Part 8: the wording is genuinely ambiguous between agreeing and asking about availability,
and the interpreter emitted no stance at all. **HOLD is the correct outcome** — the turn goes
to clarification instead of progressing, which is what an ambiguous acceptance should do. No
ACCEPT is forced.

## 7. Live runtime probe, all three flags ON

```
Si avancemos                              quote sent   ALLOW  explicit present acceptance of a delivered, current quote
Sí, avancemos                             quote sent   ALLOW  "
si me cierra te hablo                     quote sent   HOLD   no acceptance evidence in this turn
Bueno, quería revisar una 2008 del 2014   quote sent   HOLD   no acceptance evidence in this turn
gracias!                                  quote sent   HOLD   no acceptance evidence in this turn
lo voy a pensar                           quote sent   HOLD   no acceptance evidence in this turn
Sí, avancemos                             no quote     DENY   the quote was computed but never delivered
```

`crm_test.whatsapp_thread_candidates` 0 → 0 · `whatsapp_messages` 6 → 6 · OUTBOUND OFF.

## 8. Justification and audit (Part 13)

Every decision appends to `authorization_records.jsonl`: result, reason, `rule_id@version`,
risk tier, stance, **quote identity**, satisfied prerequisites, failed prerequisites,
blockers, evidence ids, cycle, revision, candidate, stage, and whether authority was on. No
customer text, no PII. Sample from the running container:

```
HOLD   authorize.quote_acceptance@v1  quote=19fa9ef31ecffee0  authority=True  acceptance is conditional…
ALLOW  authorize.quote_acceptance@v1  quote=19fa9ef31ecffee0  authority=True  explicit present acceptance…
DENY   authorize.quote_acceptance@v1  quote=19fa9ef31ecffee0  authority=True  computed but never delivered
```

## 9. Legacy path classification (Part 14)

| Site | Classification with the flag ON |
|---|---|
| `_is_acceptance` | **EVIDENCE_PRODUCER** — necessary, no longer sufficient |
| `_has_acceptance_word` | **EVIDENCE_PRODUCER / OBSERVABILITY** — cannot advance alone |
| `_handle_quoted_acceptance` | **WRITE_PATH, gated** — reached only on ALLOW |
| the three QUOTED day-proposal transitions | **WRITE_PATH, gated** by scheduling progression |
| Flow-submitted `flag=ACEPTADO` (`:1934`) | **UNCHANGED** — human-confirmed, out of scope |
| human-handoff `flag=ACEPTADO` (`:5831`) | **BUSINESS_PRECONDITION**, untouched |
| `_ALLOWED_FLAGS`, `PRESUPUESTO_ENVIADO` | **TEMPORARY_COMPATIBILITY** |

**Independent legacy acceptance write paths with the flag ON: 0** (excluding the two
non-language paths above, which are Flow and human and remain by design).

## 10. Boundaries held

Scheduling **interpretation** unchanged. Booking unchanged — `ThreadRevision(status="booked")`
is still created only by the Flow path, and the authorizer contains no booking symbol at all.
Pricing unchanged. `_handle_quoted_acceptance` still does exactly two things: `flag=ACEPTADO`
and `stage=SCHEDULING`; a test asserts it reaches no revision, no Flow button, no booked
stage and no price. L4.7D still grounds acceptance and availability claims in canonical
state — no second acceptance interpreter was created inside the validator.

## 11. Tests and regression

`tests/test_l4_7c_3b_acceptance_authority_cutover.py` — **28/28 PASS** (CUT-01…20 plus the
stance-not-word-match, no-mixed-mode, scheduling-progression-shares-prerequisites and
phrase-patch checks).

Regression: **3 503 passed / 60 failed / 9 errors** with the flag **OFF** and **ON** —
failure set identical to baseline in both positions. Launch-relevant failures: 0 new.
Unknown: 0.

One C3A assertion was realigned deliberately: it asserted that `conversation_engine.py` did
not reference the authorizer, which is precisely what this cutover changes. It now asserts
the legacy branch remains present and reachable with the flag off.

L1/L2/L3 FROZEN · L4 ACTIVE · Wild clean count **0/3**.

Next: **L4.7C.4-SCHEDULING-INTERFACE** — not automatic.
