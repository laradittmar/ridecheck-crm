PROJECT: RIDECHECK_CRM
TYPE: AUDIT
MILESTONE: L4.7W1-F1-VEHICLE-LIVE-DIVERGENCE

# A Spanish greeting was read as a Fiat Uno

Wild **STOPPED** · severity **BLOCKER** · no code changed · no tester reset · no outbound after the stop
Runtime `ridecheck-crm-backend:l4.7c4a-livesem-4ec8c43`, container unrestarted since 13:56:33Z.

---

## 0. The finding in one line

`"buen día. Bueno,"` normalises to the two-word window **`dia bueno`**, which scores **0.706**
against the catalog form **`fiat uno`** — above the 0.70 CONFIRM threshold — while the real
vehicle, **Peugeot 2008**, scored **0.632** from the window `un 2008` and lost. The greeting
outranked the car.

The semantic interpreter read the turn **correctly** — `Peugeot / 2008 / 2014` — in the same
burst. Its answer arrived **2.7 seconds after** the wrong question had already been sent, and
the vehicle path does not consult it.

## 1. Raw inbound (Part 1)

Transcription is **not** the defect. The stored transcript contains `2008 del 2014` exactly.

| # | id | wa_message_id (tail) | type | timestamp (UTC) | stored transcript |
|---|---|---|---|---|---|
| 1 | 6061 | `…RjY1QjQ2RkY5MTcxAA==` | audio | 14:02:49.309 | `Hola, buen día. Bueno, ¿era para revisar un 2008 del 2014?` |
| 2 | 6062 | `…NDE0MDI4OTg5NzI1AA==` | audio | 14:02:57.201 | `¿Cómo trabajan ustedes? ¿Mandan un informe? ¿Deben estar presentes?` |
| 3 | 6063 | `…OEZFMzU1M0MxM0JFAA==` | audio | 14:03:03.025 | `¿Aceptan? ¿Debito?` |
| — | 6064 | `…MDgzQjU2RkQwNQA=` | text **out** | 14:03:25.459 | `¿Es un Fiat Uno?` |

No second transcription exists: n8n transcribes once and posts the text; the stored value **is**
the WhatsApp-visible transcript. No unrelated PII appears in this audit.

## 2. Burst assembly (Part 2)

**All three messages, one CE turn.** `ai_events` 112 and 113 are `triggered`; 114 (message 6063)
is `processed` — the 20-second debounce collapsed the three into one burst, fired 22.4 s after
the last inbound.

* correlation / burst id `92fb5fdc-5586-4ce8-8828-b4d59f4f10c9`
* ordered text passed to CE (`current_turn_text`):
  `Hola, buen día. Bueno, ¿era para revisar un 2008 del 2014? ¿Cómo trabajan ustedes? ¿Mandan un informe? ¿Deben estar presentes? ¿Aceptan? ¿Debito?`
* cycle boundary: thread 2038 created 14:02:49 — this is the **first** cycle; there is no prior one
* CE latency 1 799 ms; reply sent 14:03:25.475, Meta wamid returned 14:03:26.907

## 3. Semantic TurnEvidence (Part 3) — it was right

Recovered from `shadow_turn_evidence.jsonl`, burst `92fb5fdc…`, `understand/1.18`,
`turn-evidence/1.2`, ok=true, latency 2 905 ms, recorded **14:03:28.148**:

```
vehicle_mentions[0]  value="Peugeot 2008"  make="Peugeot"  model="2008"  year=2014
                     catalog_candidate="Peugeot 2008"  status=PROPOSED  is_superseded=false
vehicle_mentions[1]  (year-disambiguation row) alternatives = [2008, 2014] "year candidate in burst"
service_intents      INSPECTION → PREPURCHASE_INSPECTION (PROPOSED); QUOTE_REQUEST=true (PROPOSED)
faq_intents          service_scope (CONFIRMED); payment (CONFIRMED)
acceptance null · corrections [] · ambiguities [] · conflicts [] · locations [] · scheduling []
```

Shadow reconciliation on those claims: `vehicle.make` / `vehicle.model` / `vehicle.year` all
**TRUE_ONLY → ACCEPT**, risk MEDIUM.

**The correct answer existed inside the same burst and was thrown away.** The record timestamp
(14:03:28.148) is 2 673 ms *after* the outbound send (14:03:25.475): the interpretation is
dispatched async, and — as recorded in L4.7C.4A §2 — vehicle identity consumes deterministic
evidence only. L4.7C.4A made same-turn evidence available to **scheduling** and to nothing else.

## 4. Deterministic path (Part 4) — where it went wrong

Reproduced read-only inside the live container, reproducing the logged numbers exactly:

```
lookup_vehicle(all_recent_text)          → None            (exact catalog miss)
fuzzy_lookup_vehicle(current_turn_text)  → CONFIRM  Fiat Uno  score=0.706  gap=0.074
```

which matches the live log verbatim:
`M21.1.4 fuzzy CONFIRM thread_id=2038 hit=Fiat Uno score=0.706 gap=0.074`

Full scoring table for the live turn (`_norm` → `hola buen dia bueno era para revisar un 2008 del 2014`):

| score | catalog form | full-text | n-gram | |
|---|---|---|---|---|
| **0.706** | `fiat uno` | 0.164 | **0.706** | ← winner |
| 0.632 | `peugeot 2008` | 0.185 | 0.632 | the actual vehicle |
| 0.632 | `fiat punto` | 0.190 | 0.632 | |
| 0.600 | `kia sorento` | 0.188 | 0.600 | |

Winning word-windows against `fiat uno`:

```
0.706  'dia bueno'     ← "buen día. Bueno,"
0.556  'revisar un'
0.353  'bueno era'
0.267  'un 2008'
```

**The producing function is `vehicle_catalog._best_ngram_score`**, called from
`fuzzy_lookup_vehicle` (`vehicle_catalog.py:189`). Thresholds: MEDIUM/CONFIRM 0.70, HIGH/AUTO
0.87, GAP 0.15. `0.706 ≥ 0.70` → CONFIRM; gap 0.074 < 0.15 → not AUTO_ACCEPT, so it asked
rather than silently writing a Fiat Uno candidate. That gap threshold is the only reason this
is a wrong *question* and not a wrong *canonical vehicle*.

Minimal-difference proof — remove **either** half of the greeting and the defect disappears:

| text | fuzzy outcome | score | peugeot-2008 score |
|---|---|---|---|
| `Hola, buen día. Bueno, ¿era para revisar un 2008 del 2014?` (**live**) | **CONFIRM Fiat Uno** | **0.706** | 0.632 |
| minus `Bueno,` | UNRESOLVED | 0.632 | 0.632 |
| minus `buen día.` | UNRESOLVED | 0.632 | 0.632 |
| `un 2008 del 2014` | UNRESOLVED | 0.632 | 0.632 |
| `Hola, para revisar un 2008 del 2014, ¿ustedes hacen ese servicio?` (**certified fixture**) | UNRESOLVED | 0.632 | 0.632 |

`_contextual_numeric_model_lookup` / `extract_model_del_year` / `_catalog_tipo_for` were never
reached — the CONFIRM branch returns first (§8).

## 5. Vehicle claims (Part 5)

**No vehicle `ClaimEvidence` was constructed on the live turn.** `_apply_vehicle_identity` —
the only site that builds `vehicle.make` / `vehicle.model` / `vehicle.year` / `vehicle.category`
claims — was never called, because the turn returned at the clarification before any canonical
write was attempted.

The only vehicle claims that existed anywhere were the **shadow** ones projected from the
semantic reading (`SEMANTIC_INFERRED`, producer `semantic:understand:understand/1.18`, source
message `…OEZFMzU1M0MxM0JFAA==`), carrying Peugeot / 2008 / 2014. They had no authority. There
was no conflict to omit: the deterministic side produced no claim at all.

## 6. C2 reconciliation (Part 6)

**C2 did not run.** Byte-level proof against the pre-Wild baseline:

| file | before Wild | after Wild |
|---|---|---|
| `reconciliation_records.jsonl` | 5 | **5** |
| `authorization_records.jsonl` | 12 | **12** |
| `shadow_turn_evidence.jsonl` | 47 | 48 |

Zero records for `thread_id=2038` in either authority log. The answer to the five options is
**D + E**: C2 never received the semantic evidence (that evidence is shadow-only for vehicle),
**and** another path — the M21.1.4 fuzzy clarification — produced the response and returned
before the reconciled write path was reached. C2 was not overruled; it was never invoked.

## 7. Write path (Part 7)

**No `WhatsAppThreadCandidate` was created.** `select * from whatsapp_thread_candidates` → 0 rows.
Nothing mutated, nothing to preserve. What *was* written to `whatsapp_thread_states` (thread 2038):

```
pending_fuzzy_catalog_key    = 'Fiat||Uno'
pending_turn_evidence_text   = 'Hola, buen día. Bueno, ¿era para revisar un 2008 del 2014? …'
last_stage = QUALIFYING · last_intent = <empty> · needs_human = false
current_focus_candidate_id = NULL · current_revision_id = NULL · home_zone_group = NULL
```

This is the live hazard the Wild stopped in time: a `Sí` from the customer would have replayed
that pending key through the M21.2 acceptance path and created a **canonical Fiat Uno candidate**
carrying the year and zone re-extracted from the real burst.

## 8. Response origin (Part 8)

```
conversation_engine.py:3101   _fuzzy = fuzzy_lookup_vehicle(current_turn_text)
conversation_engine.py:3127   log "M21.1.4 fuzzy CONFIRM … hit=Fiat Uno score=0.706 gap=0.074"
conversation_engine.py:3131   return self._handle_fuzzy_confirm(...)          ← the turn ends here
conversation_engine.py:5167   question = _FUZZY_CONFIRMATION_TEMPLATE.format(marca, modelo)
conversation_engine.py:634    _FUZZY_CONFIRMATION_TEMPLATE = "¿Es un {marca} {modelo}?"
conversation_engine.py:5171   self._send_text_to_wa(ctx, question)
```

decision type `VEHICLE_RESOLVER` · clarification reason: fuzzy CONFIRM on exact-catalog miss ·
alternatives: runner-up Fiat Punto at 0.632, gap 0.074.

**The structural defect.** Sixty-seven lines *below* the `return`, the WILD-02-B numeric-model
clarification (`conversation_engine.py:3194`) formats the **same template** and is the certified
producer of `¿Es un Peugeot 2008?` for exactly this input. It is unreachable whenever the fuzzy
scorer returns CONFIRM. Two deterministic clarification producers compete for one turn, and the
weaker one — free-text similarity — is ordered first and returns.

L4.7D did not alter the response: the sent text equals the template output character for
character, and `_handle_fuzzy_confirm` calls `_send_text_to_wa` directly, bypassing composition.

Outbound ledger row 6064: `path_id=CE_TEXT`, `deployment_id=4ec8c43`,
`correlation_id=92fb5fdc-5586-4ce8-8828-b4d59f4f10c9`, fingerprint `d70d84d28496…`,
`OUTBOUND_GATE_ALLOWED` → `OUTBOUND_GATE_SENT`, Meta 200, status `read`. The gate behaved
correctly throughout; **0 security events** were raised. The send was authorized — it was the
*content* that was wrong.

## 9. Test vs live (Part 9)

`tests/test_l4_6_evidence_capture.py` — **28/28 pass, unmodified**, and asserts
`¿Es un Peugeot 2008?`.

| layer | TEST PATH | LIVE PATH | diverges? |
|---|---|---|---|
| input | `Hola, para revisar un 2008 del 2014, ¿ustedes hacen ese servicio?` | `Hola, buen día. Bueno, ¿era para revisar un 2008 del 2014?` + 2 more | text differs |
| burst | single fixture string | 3 audio messages, debounced | no effect (§4: msg 1 alone scores 0.706) |
| TurnEvidence | not exercised | Peugeot 2008 / 2014, correct | not consulted either way |
| `lookup_vehicle` | None | None | same |
| **`fuzzy_lookup_vehicle`** | **UNRESOLVED 0.632** | **CONFIRM Fiat Uno 0.706** | **★ FIRST DIVERGENCE** |
| claims | via `_enforce_canonical_vehicle_claim` | none built | consequence |
| reconciliation | Peugeot\|\|2008 armed | Fiat\|\|Uno armed | consequence |
| candidate | none (pending armed) | none (pending armed) | same shape, wrong value |
| response | `¿Es un Peugeot 2008?` | `¿Es un Fiat Uno?` | consequence |

**First divergence: `vehicle_catalog.fuzzy_lookup_vehicle` / `_best_ngram_score`.** Everything
upstream — Whisper, webhook, burst assembly, storage, exact catalog lookup — is identical.

Why the certified suite never caught it: its fixture `WILD_B_T1` scores 0.632 and therefore
**never enters the CONFIRM branch at all**, and the tests that assert `¿Es un Peugeot 2008?`
call `_enforce_canonical_vehicle_claim` and `_process_text`-with-pending-state directly. No
certified test drives a real greeting through `fuzzy_lookup_vehicle` on the pre-AI branch. The
gap is in fixture realism, not in assertion strength.

## 10. FAQ coexistence (Part 10) — classified separately

Four questions were asked. **None was answered.** Two independent causes:

**(a) The turn ended early.** `_handle_fuzzy_confirm` sends via `_send_text_to_wa` directly, so
the F3 FAQ-supplement stage (`conversation_engine.py:2066`) was never reached.

**(b) Even if reached, deterministic FAQ detection misses all four.** The detectors are literal
phrase sets; the customer's wording matches none:

| customer said | detector set | contains | result |
|---|---|---|---|
| `¿Cómo trabajan ustedes?` | — | no service_scope phrase exists | **MISS** |
| `¿Mandan un informe?` | `_REPORT_FAQ_DETECTION` | `mandan informe` (no `un`) | **MISS** |
| `¿Deben estar presentes?` | `_PRESENCE_FAQ_DETECTION` | `tengo/hay que/necesito estar presente` | **MISS** |
| `¿Aceptan? ¿Debito?` | `_PAYMENT_FAQ_DETECTION` | `aceptan debito` as one phrase | **MISS** (split across sentences) |

The **semantic** layer detected `service_scope` and `payment`, both `CONFIRMED`. Canonical
answers exist and were never used (`_FAQ_REPORT_ANSWER`, `_FAQ_PRESENCE_ANSWER`,
`_FAQ_PAYMENT_ANSWER`).

Payment/debit handling is present in the codebase and absent from the conversation. Classified
**HIGH, product-value**, separate from the vehicle BLOCKER. Not fixed.

## 11. Stale state (Part 11) — none

Thread 2038 and contact 2045 were created at 14:02:49, after the L4.7W1 zero-state reset
(verified 0/0/0/0/0/0/0/0/0/0 before the Wild). At the moment of the defect:
no candidate (0 rows), no revision, no thread_revision, no quote, no `home_zone_group`,
`cycle_reset_pending=false`, `last_intent` empty, first cycle, and the shadow evidence file
contained **no** prior record for thread 2038. Semantic context keys sent were only
`current_local_date, current_weekday, timezone, stage`.

**`Fiat Uno` was manufactured inside this turn, from this text, by string similarity.** Nothing
carried it in.

## 12. Evidence bundle

`/opt/ridecheck-crm-forensics/` — sealed in `L4.7W1F1_manifest.sha256`:

```
e05e7590…  L4.7W1F1_backend_stdout_2026-09-04T141552Z.log   (383 lines, container unrestarted)
46e72c1a…  L4.7W1F1_turn_evidence_2038.json                 (the live TurnEvidence record)
e3b0c442…  L4.7W1F1_n8n_stdout_2026-09-04T141552Z.log       (empty — n8n logs nothing at this level)
d372f33e…  L4.7W1_tester_export_pre_reset_2026-09-04T135454Z.txt  (pre-Wild zero-state proof)
```

Outbound remains **armed** (`OUTBOUND_ENABLED=true`, allowlist `…8330`). I did not flip it:
recreating the container destroys its log stream, and the stop condition forbids continuing the
Wild, not leaving the switch as the owner set it. Disabling it is a one-command owner decision;
the logs are already captured, so it is now safe to do.

---

**No speculation is offered as to the fix.** The first divergence is proven, the producer is
named, and the surface is finite.
