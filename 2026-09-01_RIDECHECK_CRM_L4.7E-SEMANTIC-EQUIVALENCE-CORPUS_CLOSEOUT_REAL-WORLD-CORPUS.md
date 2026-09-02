PROJECT: RIDECHECK_CRM
TYPE: CLOSEOUT
MILESTONE: L4.7E-SEMANTIC-EQUIVALENCE-CORPUS

# L4.7E — Durable semantic corpus, truth model and evaluation harness

Date: 2026-09-01 (resumed after L4.7-SOURCE-RECOVERY)
No runtime behaviour changed · no CE reordering · no prompt/model change · no Wild ·
OUTBOUND OFF · crm_test only · production DB untouched.

---

## 1. Verification of the already-committed corpus (nothing recreated)

| Check | Result |
|---|---|
| Owner-provided real examples | **4/4 present, byte-verbatim** (REAL-001…REAL-004; lengths 93, 62, 115, 200) |
| Failed/known Wild examples | **8** — WILD-A-01…04, WILD-B-01, WILD-B-02, WILD-01-01, WILD-01-02 |
| Other real historical cases | 0 (the eight Wild entries are the complete set whose raw text survives in committed artefacts; nothing was fabricated for missing text) |
| Synthetic cases | **150** |
| **Total** | **162** — unchanged from the committed state |
| Unique ids | yes |
| Equivalence groups | 12 (A–L); A=27, B=24, C=23, D=10, E=21, F=10, G=13, H=9, I=8, J=11, K=12, L=11 |
| `owner_review_required` | REAL-002 (noisy ASR-like), REAL-004 (mobility offer → pricing/coverage is a business call) |
| `failure_class` recorded | WILD-A-04 (SCHED-A/B), WILD-B-01 (VEH-A/B), WILD-B-02 (LOC-A), WILD-01-01 (DEFECT-WILD-01-A) |

No owner-provided raw message was rewritten; a test now enforces that permanently.

## 2. Truth model verification

`docs/semantic/SEMANTIC_TRUTH_MODEL.md` already documents, and was left unchanged:

- the three layers **RAW EVIDENCE → TURN EVIDENCE → CANONICAL STATE**, with the rules that
  no layer silently replaces another, RAW stays reconstructable, TURN EVIDENCE stays
  auditable and CANONICAL STATE records how each value was accepted;
- the status model **CONFIRMED / PROPOSED / AMBIGUOUS / CONFLICT**, with AMBIGUOUS and
  CONFLICT never producing canonical values;
- the provenance contract (field, value, role, source message/burst/span, interpreter,
  confidence, status, schema version, model version, reconciliation result);
- privacy rules, the replay contract and the metric definitions.

It was verified as correct and **not redesigned**.

## 3. Evaluation harness (new)

`tests/semantic_corpus/evaluation.py` — deliberately inert: no OpenAI, no database, no
`conversation_engine` import (asserted by a test). It scores *meaning*, not helper output:

```
interpreter(messages) -> {"turn_evidence": [{field, value, status, role?}, …],
                          "canonical_state": {…}}
```

Metrics, reported separately (no single opaque score) and sliceable by REAL/SYNTHETIC and
by equivalence group:

| Metric | Definition |
|---|---|
| field precision | proposed items that are correct ÷ proposed |
| field recall | expected items proposed ÷ expected |
| role accuracy | correct role ÷ items where a role is expected (inspection location vs customer origin) |
| unsupported-inference rate | cases violating a `must_not_infer` contract ÷ cases — the safety metric, target 0 |
| ambiguity/conflict handling accuracy | AMBIGUOUS/CONFLICT expectations honoured (not forced into a value) ÷ such expectations |
| missing-field accuracy | fields expected to stay unknown that stayed unknown |

Semantics worth noting: ordered scheduling alternatives are compared **in order**, so a
swapped primary/fallback or a transplanted time scores as a mismatch — the Wild A failure
is directly measurable; proposing "unknown" is never counted as an invention; and a value
appearing in either proposed evidence or proposed canonical state can trigger a
`must_not_infer` violation.

## 4. Corpus tests (new)

`tests/test_l4_7e_semantic_corpus.py` — **33/33 PASS**, covering:

- schema validity of every case, unique ids, provenance present, REAL vs SYNTHETIC marking;
- owner examples byte-verbatim; Wild imports carry source and failure class;
- all statuses valid; unresolved evidence carries no value; canonical state never
  contradicts unresolved evidence;
- every `must_not_infer` rule states a reason and never forbids what the same case
  requires;
- `owner_review_required` present exactly where truth is genuinely uncertain, and
  REAL-001/002 keep vehicle and location unresolved (no artificial certainty);
- all 12 groups present, ≥20 variants for intent, vehicle, location role, acceptance and
  scheduling; location-role cases forbid the origin as inspection location;
- no PII in the corpus (tester wa_id, gmail, WAMIDs, token prefixes all absent);
- harness behaviour against perfect / lazy / hallucinating stubs: role errors detected,
  unsupported inference caught (the exact Wild B failure), recall and precision losses
  counted, ambiguity forcing penalised, scheduling order enforced, slices produced.

## 5. Original message persistence audit (actual implementation)

| Question | Answer |
|---|---|
| RAW MESSAGE STORED | **YES** |
| TABLE/MODEL | `whatsapp_messages` / `app.models.WhatsAppMessage` — `wa_message_id` (unique), `thread_id`, `direction`, `timestamp`, `created_at`, `message_type`, `media_id`, `text`, `raw_payload` (JSONB: `id`, `from`, `type`, `audio`, `timestamp`, `from_user_id`) |
| RAW BURST RECONSTRUCTABLE | **PARTIAL** — chronology is exact (`thread_id` + `timestamp` + `id`), and `whatsapp_outbound_dedup.causal_inbound_wa_message_id` plus `whatsapp_thread_states.last_processed_inbound_wa_message_id` recover which inbound closed each turn. The n8n 20-second debounce grouping itself is not persisted, so burst boundaries for turns that produced no send are inferred. Proven end to end on thread 2037. |
| MESSAGE ORDER PRESERVED | **YES** — `timestamp` (tz-aware) and monotonic `id` |
| WA MESSAGE ID PRESERVED | **YES** — `wa_message_id`, unique constraint; production: 14/14 inbound rows carry one |
| CURRENT RETENTION RISK | **MEDIUM.** (a) For audio, `text` holds a **derived Whisper transcript** written by `POST /integrations/whatsapp/media/{id}/transcribe`, which overwrites `text` on re-transcription — the transcript is TURN-layer evidence, not RAW; the audio itself is not retained (only `media_id`; Meta media URLs expire). All four Wild B inbound rows are `message_type='audio'`. (b) Production has 3 of 14 inbound rows without `raw_payload` and 2 without `text` (older rows). (c) Tester-reset procedures delete crm_test message rows (L4.2/L4.4) — mitigated by the forensic exports, not by the database. (d) No retention policy or TTL is defined. |

## 6. Historical replay contract — proven, non-mutating

`tests/semantic_corpus/replay_demo.py` executes the documented path against real stored
data and was run read-only against `crm_test` thread 2037:

```
thread 2037: 6 rows, 4 inbound messages reconstructed in order
  [1] Hola, para revisar un 2008 del 2014, ¿ustedes hacen ese servicio?
  [2] ¿Entregan informes? ¿Qué contenido tienen los informes? ¿Tengo que estar yo presente?
  [3] ¿Se puede pagar con débito?
  [4] Está en Berazategui, pero yo soy de Tigre.
scored against WILD-B-02: tp=0 fp=0 fn=2 unsupported=0
NO CRM state was mutated.   (message count 6 before and after)
```

The session is opened read-only, rolled back explicitly and closed; rows are copied into
plain namespaces so nothing can be flushed back; `ConversationEngine` is never imported.

**Online self-learning remains DISALLOWED.** The permitted loop is unchanged: real
conversation → anonymised/labelled corpus → offline evaluation → prompt/schema/model
improvement → regression certification → controlled deployment.

## 7. Roadmap

`docs/launch/LAUNCH_TRUTH_ROADMAP.md` records L4.7E as PASS and the agreed sequence:

```
L4.7E → L4.7A TurnEvidence schema → L4.7B shadow UNDERSTAND
      → L4.7B.1 replay/disagreement analysis → L4.7C reconciler migration
      → L4.7F certification → Clean Wild C
```

L1/L2/L3 FROZEN · L4 ACTIVE · Wild clean count **0/3** · no new Wild.

## 8. Safety

No ConversationEngine change, no OpenAI prompt or model change, no pipeline reordering, no
live send, OUTBOUND OFF, crm_test only, production untouched, no secrets committed.
