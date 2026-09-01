PROJECT: RIDECHECK_CRM
TYPE: AUDIT
MILESTONE: L4-WILD-B-VEHICLE-FORENSIC

# L4 Wild B — Vehicle Recognition / State-Persistence Forensic

Date: 2026-09-01
Scope: Wild B turns 1–2 on a verified true zero-state tester (L4.4), runtime image
`ridecheck-crm-backend:l4.3-sched-103dd01`, deployment `103dd01ca7b5`, DB crm_test.
Constraints honoured: Wild B stopped, no WhatsApp message sent, no DB mutation, no manual
candidate, no location replay, no code changes, no scheduling continuation.

---

## 0. Immediate safety actions (in order)

1. Backend logs captured **before** any container recreate →
   `/opt/ridecheck-crm-forensics/L4-WILD-B_backend_logs_2026-09-01T205142Z.txt`
   (473 lines; sha256 `0b8625ff…7a7dec6`).
2. Full DB state exported →
   `L4-WILD-B_db_state_export_2026-09-01T205142Z.txt` (sha256 `a5a8be35…9347e31`);
   hashes in `L4-WILD-B_evidence_2026-09-01T205142Z.sha256`.
3. Only then OUTBOUND disabled — backend recreated 20:51:53Z, `OUTBOUND_ENABLED=false`.
4. Nothing else touched: 6 messages, 0 candidates, 733 security_events, thread state
   unchanged, pending vehicle question left unanswered.

---

## 1. PART 1 — Preserved evidence

| Item | Value |
|---|---|
| Contact | 2044 · wa_id 549115***8330 · created 20:29:57Z |
| Thread | 2037 · lead_id 123 |
| Lead | 123 · estado CONSULTA_NUEVA |
| ThreadState | 2037 · last_intent PREPURCHASE_INSPECTION · last_stage **QUALIFYING** · current_focus_candidate_id **NULL** · home_zone_group/detail **NULL** · pending_fuzzy_catalog_key **NULL** · pending_turn_evidence_text **NULL** · vehicle_clarification_sent **false** · cycle_reset_pending false |
| Candidates | **0 rows** |
| Messages | 6 (4 in / 2 out), both outbound `CE_TEXT`, status `read`, deployment_id `103dd01ca7b5`, correlation_id populated |
| ai_events | 3 rows (`inbound_message`; the table stores metadata only — no AI payload) |
| n8n | 4 inbound executions in the window; 2 CE invocations at 20:30:47Z and 20:31:35Z |
| SecurityEvents | 0 new (none since id 734) |
| Revisions / ThreadRevisions | 0 / 0 |

**Forensic observability gap:** the container log contains only uvicorn access lines — no
CE `logger.info` output (`M18…`, `M21…`, `WILD02…`) is emitted at runtime. Every conclusion
below therefore had to be reconstructed by re-executing the deployed code against the exact
message texts, not by reading decisions from logs.

Transcript:

| id | time | dir | text |
|---|---|---|---|
| 6055 | 20:29:57 | in | "Hola, para revisar un 2008 del 2014, ¿ustedes hacen ese servicio?" |
| 6056 | 20:30:06 | in | "¿Entregan informes? ¿Qué contenido tienen los informes? ¿Tengo que estar yo presente?" |
| 6057 | 20:30:20 | in | "¿Se puede pagar con débito?" |
| 6058 | 20:30:45 | out | "¡Hola! Sí, hacemos el servicio de revisión para un 2008 del 2014. Entregamos un informe detallado…" (+ zone question) |
| 6059 | 20:31:09 | in | "Está en Berazategui, pero yo soy de Tigre." |
| 6060 | 20:31:34 | out | "Genial! Para poder cotizar, ¿podrías confirmarme el tipo de vehículo que querés revisar? En este caso, un 2008 del 2014. ¿Es correcto?" |

---

## 2. PART 2 — First-turn vehicle trace

Deterministic re-execution inside the **deployed** container against the exact burst text:

```
extract_model_del_year("Hola, para revisar un 2008 del 2014, ¿ustedes hacen ese servicio? …")
  → (VehicleMatch(Peugeot 2008, SUV_4X4_DEPORTIVO, confidence=medium, alias='2008'), 2014)
_contextual_numeric_model_lookup("2008") → Peugeot 2008 / SUV_4X4_DEPORTIVO
lookup_vehicle(<same burst>)             → NO MATCH   (bare "2008" is not a catalog alias)
```

So the evidence extractor **does** resolve the vehicle. It was never reached, because the
CE gate in front of it evaluated False:

```python
_numeric_model_ctx = (
    state.last_intent in (_AWAITING_QUALIFICATION, _INTENT_PREPURCHASE)      # turn 1: NULL
    or (state.last_stage in (STAGE_QUALIFYING, None)
        and (self._detect_prepurchase_signal(_text_norm_b)                    # ← False
             or self._detect_explicit_inspection_request(_text_norm_b)))      # ← False
)
```

Measured on the deployed code:

| Turn-1 text | `_detect_prepurchase_signal` | `_detect_explicit_inspection_request` | gate |
|---|---|---|---|
| Wild A "…**quería revisar** una 2008 del 2014…" | **True** (`quería revisar`) | **True** (`\b(quiero\|queria\|necesito\|quisiera)\s+revis[ae]r\s+(un[ao]?\|el\|la\|este\|ese)\b`) | OPEN |
| Wild B "Hola, **para revisar** un 2008 del 2014…" | **False** | **False** | **CLOSED** |

Answers:

- DID CE UNDERSTAND MODEL 2008? **NO** — not canonically. The extractor can, but was not invoked.
- DID CE UNDERSTAND YEAR 2014? **NO** — same gate.
- DID CE RESOLVE PEUGEOT 2008? **NO** in runtime (**YES** when the extractor is called directly).
- DID CE RESOLVE VEHICLE CATEGORY? **NO** in runtime (`SUV_4X4_DEPORTIVO` is available from the catalog).
- WAS A CANDIDATE PROPOSED? **NO** by any deterministic path; the AI did not create one either.
- WAS A CANDIDATE PERSISTED? **NO** — `whatsapp_thread_candidates` is empty.

**WHY EXACTLY:** both WILD-02-B (numeric-model clarification) and WILD-04-F1
("model del year" extraction) sit behind the same `_numeric_model_ctx` guard, which
requires either an already-established inspection intent or an intent phrase from a
closed whitelist. "para revisar un 2008 del 2014" is a purpose clause with no modal verb
(`quiero/quería/necesito/quisiera`) and is not sentence-initial, so it matches neither
`_PREPURCHASE_SIGNALS` nor `_INSPECTION_REQUEST_PATTERNS`. The gate closed, the extraction
never ran, and the burst fell through to the AI as a FAQ-dominant turn.

---

## 3. PART 3 — Why the reply claimed understanding

Message 6058 states "hacemos el servicio de revisión **para un 2008 del 2014**" while the
canonical state held no candidate at all. The source is the **AI composer** echoing the
customer's own words: no deterministic path produced a vehicle for this turn, no candidate
row exists, `vehicle_clarification_sent=false`, and the reply is not any deterministic
template. Message 6060 ("¿podrías confirmarme el tipo de vehículo…?") is likewise
AI-composed, not the deterministic confirmation path — proven by
`pending_fuzzy_catalog_key = NULL` (the deterministic path always sets it).

Classification: **both** — a state-persistence defect (VEH-A) *and* a response/state
inconsistency (VEH-B). The bot spoke as though vehicle identity was established while
canonical state considered it unresolved, and it did so twice in two turns with
contradictory postures (turn 1 "yes, for your 2008 of 2014"; turn 2 "please confirm which
vehicle").

---

## 4. PART 4 — Wild A vs Wild B

Everything in the runtime is identical except the customer's wording. The deployed image,
catalog, gate code and models are the same; L4.3 touched only scheduling parsing, the
Booking-Flow sender, the rejection composer, the FAQ hours source and outbound
attribution — **none of the vehicle or location paths**.

| | Wild A (pre-L4.3 image) | Wild B (l4.3 image) |
|---|---|---|
| Turn-1 phrasing | "quería revisar una 2008 del 2014" | "para revisar un 2008 del 2014" |
| `_detect_prepurchase_signal` | True | **False** |
| `_detect_explicit_inspection_request` | True | **False** |
| WILD-04-F1 extraction reached | yes | **no** |
| Candidate persisted | yes (Peugeot 2008 / 2014 / SUV_4X4_DEPORTIVO) | **no** |
| Turn-2 phrasing | "El auto está en Berazategui. Yo soy de Tigre." | "Está en Berazategui, pero yo soy de Tigre." |
| `_extract_vehicle_location_zones` | `[Sur/Berazategui]` | **`[]`** |
| Zone stored | candidate zone Sur/Berazategui | **nothing** |
| Quote | $240.000 produced | none |

"una" vs "un" is irrelevant — `extract_model_del_year` returns the same match for both.
The determinant is the intent phrase, and secondarily the presence of an explicit vehicle
subject in the location sentence.

**Verdict: L4.3 did not cause this. It is a pre-existing coverage boundary that a
different, equally natural phrasing walked straight through.**

---

## 5. PART 5 — Model-only vehicle semantics

Measured on the deployed catalog:

| Input | `lookup_vehicle` | `extract_model_del_year` |
|---|---|---|
| "un 2008 del 2014" | NO MATCH | **Peugeot 2008 + 2014** |
| "una 2008 del 2014" | NO MATCH | **Peugeot 2008 + 2014** |
| "Peugeot 2008 2014" | Peugeot 2008 / SUV_4X4_DEPORTIVO / high | — |
| "quiero revisar un 2008" | NO MATCH | None (no "del year") — `_contextual_numeric_model_lookup` covers it |
| "2008" | NO MATCH | — (`_contextual_numeric_model_lookup("2008")` → Peugeot 2008) |
| "una Taos 2020" | Volkswagen Taos / SUV_4X4_DEPORTIVO / high | — |
| "un Focus 2017" | Ford Focus / AUTO / high | — |

Model-only recognition is genuinely supported: alphabetic models resolve through
`lookup_vehicle`, numeric models through `_contextual_numeric_model_lookup` /
`extract_model_del_year`, with sane ambiguity guards ("2008 o 2014" → None). No hardcoded
2008 alias is needed or wanted.

**MODEL-ONLY VEHICLE SUPPORT: PASS at the catalog layer, FAIL at the CE gate layer.** The
capability exists and is correct; the routing condition prevents it from being used.

---

## 6. PART 6 — Location turn trace

Deterministic re-execution on "Está en Berazategui, pero yo soy de Tigre.":

```
_extract_vehicle_location_zones(...)  → []                       ← no explicit vehicle subject
_extract_zone_from_text(...)          → Sur / Berazategui         ← the data IS recoverable
_has_customer_origin_clause(...)      → True                      ← "yo soy de Tigre"
_detect_vehicle_location_phrase(...)  → True                      ← "está en"
```

In `_apply_zone_from_text` this combination reaches neither write path:

```python
if _vlzones:                      # [] → skipped (candidate/state write)
    …
elif not _has_customer_origin_clause(text):   # True → elif skipped (bare-locality fallback)
    …
return None, False                # nothing written anywhere
```

Comparison: Wild A's "**El auto** está en Berazategui." produced `[Sur/Berazategui]`, so the
candidate received the zone. A bare "Está en Berazategui." (without an origin clause) also
works, through the fallback branch. Only the **combination** — subjectless vehicle-location
clause *plus* a customer-origin clause in the same message — drops the evidence entirely.

Answers:

- WAS BERAZATEGUI EXTRACTED? **NO** as vehicle-location evidence (`[]`); **YES** as bare
  locality (`Sur/Berazategui`), but that branch was suppressed.
- WAS TIGRE EXTRACTED? Correctly recognised as a **customer-origin clause** and never applied
  as inspection location — the LR-2 separation held.
- WAS BERAZATEGUI STORED ANYWHERE? **NO** — `home_zone_group`/`home_zone_detail` are NULL and no
  candidate exists.
- WAS LOCATION MUTATION BLOCKED BECAUSE NO CANDIDATE? **NO** — the pre-candidate fallback
  (`state.home_zone_*`) exists and would have accepted it; it was never reached.
- WAS IT DROPPED? **YES.**

---

## 7. PART 7 — Confirmation replay risk

The pending question is AI-generated, so CE holds **no confirmation state**:
`pending_fuzzy_catalog_key = NULL`, `pending_turn_evidence_text = NULL`.

Deterministic reproduction of a "Sí" reply against the deployed code:

1. The fuzzy-acceptance branch is guarded by
   `if getattr(state, "pending_fuzzy_catalog_key", None) and not state.needs_human:` → NULL → **skipped**.
   No candidate is created from a stored key, and no zone/year replay occurs
   (`_apply_pending_turn_evidence` is only invoked on that path).
2. `extract_model_del_year("Sí")` → None; `_contextual_numeric_model_lookup("Sí")` → None →
   no deterministic candidate creation.
3. Nothing holds Berazategui: state zone is NULL, no candidate exists,
   `pending_turn_evidence_text` is NULL. The only trace is message 6059 in history.

**Answer: (C) — neither A nor B.** CE has no pending-confirmation machinery armed at all, so
"Sí" is handled entirely by the AI. A candidate may or may not be created (non-deterministic),
and the inspection location is preserved by **no** deterministic mechanism. In practice the
customer must repeat Berazategui, or the system quotes on a location it re-derived from free
history rather than from authoritative evidence.

**Classification: HIGH.**

---

## 8. PART 8 — Intended business authority rule

1. If current-turn evidence uniquely identifies a known catalog vehicle — including a
   numeric model with a companion year ("2008 del 2014") — **persist the candidate
   immediately**. Recognition must not depend on the customer using a modal verb.
2. Confirmation is reserved for genuinely ambiguous interpretation (multiple year-shaped
   tokens, typo/fuzzy hits below threshold, competing catalog entries) — not for a missing
   make when the model uniquely determines make and category.
3. Customer-facing copy must reflect canonical state: speak of an established vehicle only
   when a candidate exists; otherwise ask explicitly instead of implying the vehicle is known.
4. Vehicle-location evidence must be captured whenever a location clause is present and the
   sentence is not exclusively about customer origin — the absence of an explicit subject
   ("el auto") must not discard it, and a same-message origin clause must suppress only the
   origin, never the vehicle location.

---

## 9. PART 9 — Regression coverage gap

| Layer | What exists | Why it missed this |
|---|---|---|
| `extract_model_del_year` unit tests (W4F1-01…06) | `extract_model_del_year("un 2008 del 2014")` asserted directly | They bypass the CE gate entirely — the extractor was always correct |
| CE-level WILD-04-F1 tests (W4F1-07…12) | bursts like "**Quiero** revisar un 2008 del 2014", "Hola, ¿cómo va? **Quiero** revisar un 2008 del 2014" | Every fixture uses whitelisted wording, so `_numeric_model_ctx` is always True |
| L4.1 / WILD-01 fixtures | "**Quería** revisar un 2008 del 2015" | Same whitelist |
| Location fixtures (M21.2, L3) | "Está en Palermo." **alone**, or "**El auto** está en X" | Never the combination subjectless-location + origin clause in one message |
| L4.2 / L4.4 clean-slate rehearsals | assert absence of inherited state | They never drive a real first inbound through candidate creation |

**Exact gap:** no test drives a first inbound whose wording falls *outside* the intent
whitelist, and no test asserts DB candidate persistence for a non-whitelisted phrasing.
Likewise no test asserts zone persistence for a subjectless location clause combined with a
customer-origin clause.

---

## 10. PART 10 — Gate classification

| ID | Severity | Gate | Description | Evidence |
|---|---|---|---|---|
| **VEH-A** | HIGH | L4 | Uniquely identifiable model+year ("2008 del 2014") not persisted because WILD-02-B/WILD-04-F1 sit behind an intent-phrase whitelist; "para revisar…" is outside it | deployed-code re-execution: extractor returns Peugeot 2008/2014; both detectors False; 0 candidate rows |
| **VEH-B** | HIGH | L4 | Reply asserted the vehicle ("hacemos el servicio para un 2008 del 2014") while canonical state had no candidate; next turn asked the customer to confirm the same vehicle | msgs 6058/6060 vs 0 candidates, `pending_fuzzy_catalog_key=NULL` |
| **LOC-A** | HIGH | L4 | Inspection location dropped: subjectless "Está en Berazategui" yields no vehicle-location zone, and the same-message origin clause suppresses the bare-locality fallback | `_extract_vehicle_location_zones` → `[]`, `_has_customer_origin_clause` → True, `home_zone_*` NULL |
| **LOC-B** | HIGH | L4 | No confirmation state is armed, so a "Sí" cannot replay the location; nothing holds Berazategui | `pending_fuzzy_catalog_key`/`pending_turn_evidence_text` NULL |
| **OBS-A** | MEDIUM | L4 | CE decision logging is invisible at runtime (uvicorn access lines only), so the reconstruction required re-executing code rather than reading logs | preserved log: 473 lines, zero CE INFO records |

**CONTRADICTORY EVIDENCE AGAINST A FROZEN GATE: NO.**

- **L1 (semantic authority)** governs current-turn evidence versus stale/AI-derived data
  overwriting it. Nothing was overwritten here: Tigre correctly did **not** become the
  inspection location, and no stale value appeared. The failure is that evidence was never
  *captured*, which is outside L1's certified invariant. **L1 stays FROZEN** — but the L1
  truth-table row "explicit current-turn zone vs AI overwrite" should be read narrowly: it
  guarantees precedence, not extraction coverage.
- **L3 (dirty history)** — the thread had no history at all. Not contradicted.
- **L2** — transport, path attribution and forensic fields were correct
  (`CE_TEXT`, deployment_id `103dd01ca7b5`, correlation_id populated). Not contradicted.
- **L4** owns all findings.

---

## 11. Root cause

**Deterministic vehicle-evidence extraction is gated behind an intent-phrase whitelist
rather than behind the evidence itself.** "para revisar un 2008 del 2014" is an ordinary
purpose clause with no modal verb, so `_numeric_model_ctx` closed and both numeric-model
paths were skipped, leaving the AI to answer conversationally without any canonical state.
The location turn then hit a second, independent coverage boundary — a subjectless
vehicle-location clause combined with a customer-origin clause writes nothing — so the
conversation now holds neither a vehicle nor a location, while having twice told the
customer it understood the vehicle.

---

## 12. Recommended finite remediation — L4.6-EVIDENCE-CAPTURE

No code was changed in this audit. Proposed scope, narrow and testable:

**A. Evidence-first vehicle gate (VEH-A).**
Run `extract_model_del_year` / `_contextual_numeric_model_lookup` whenever the thread has no
candidate and the stage is QUALIFYING/NULL, regardless of intent phrasing; keep intent
detection for *routing/tone*, not for whether evidence is captured. Keep the existing
ambiguity guards (multi-year tokens, generic vehicle words) untouched. Optionally broaden
`_INSPECTION_REQUEST_PATTERNS` with the purpose-clause form (`para/quiero…revisar un…`), but
the gate change is the fix — the pattern list is a mitigation, not a contract.

**B. Response/state consistency (VEH-B).**
Before composing, if no candidate exists, the reply must not restate a vehicle as
established. Add a deterministic finalizer (the `_apply_required_next_question` mechanism
already exists) that, when stage is QUALIFYING and no candidate exists, forces an explicit
vehicle question and strips any implied confirmation.

**C. Location capture (LOC-A).**
When a vehicle-location phrase is detected but `_extract_vehicle_location_zones` returns
nothing, fall back to bare-locality extraction *excluding* the origin clause span, instead of
skipping on `_has_customer_origin_clause`. Write to `state.home_zone_*` when no candidate
exists — the buffer already exists and is designed for exactly this.

**D. Confirmation-state contract (LOC-B).**
Any vehicle-confirmation question must be deterministic and must arm
`pending_fuzzy_catalog_key` + `pending_turn_evidence_text`. If the AI is allowed to ask it,
CE must arm the same state so acceptance replays evidence. Alternatively forbid AI-authored
confirmation questions in QUALIFYING.

**E. Observability (OBS-A).**
Emit CE decision logs at runtime (logging config), so the next Wild is reconstructable from
logs rather than by re-executing code.

**F. Tests.**
`tests/test_l4_6_evidence_capture.py`: the exact Wild B burst → candidate persisted with
Peugeot 2008 / 2014 / SUV_4X4_DEPORTIVO; the exact Wild B location turn → `home_zone_*` =
Sur/Berazategui with Tigre not applied; a phrasing matrix (purpose clause, modal, bare
imperative, subjectless location ± origin clause) asserting **DB persistence**, not parser
output; and a reply-consistency assertion that no reply claims a vehicle while no candidate
exists.

Wild B stays PAUSED and the clean-Wild counter stays 0/3 until A–D land and a new controlled
Wild proves them.
