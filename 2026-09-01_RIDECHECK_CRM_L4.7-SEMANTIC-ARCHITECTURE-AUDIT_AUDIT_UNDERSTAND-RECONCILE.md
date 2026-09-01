PROJECT: RIDECHECK_CRM
TYPE: AUDIT
MILESTONE: L4.7-SEMANTIC-ARCHITECTURE-AUDIT

# L4.7 — Semantic Interpretation Architecture Audit

Date: 2026-09-01
Runtime audited: `ridecheck-crm-backend:l4.6-evidence-d73ebf9` (deployment `d73ebf951966`),
crm_test, OUTBOUND OFF. Read-only: no code changes, no DB writes, no Wild.
All line references are to `backend/app/services/conversation_engine.py` unless stated.

---

## 1. PART 1 — Actual runtime execution order

`n8n` (transport: transcription, image description, 20 s debounce, burst assembly, lead
find/create) → `POST /api/conversation/handle` → `api/conversation.py` →
`ConversationEngine.handle()` (1601) → `_handle()` → `_process_text()` (2648).

Inside `_process_text`, in real execution order:

| # | Step | Location |
|---|---|---|
| 1 | current-turn evidence assembly (`current_turn_text`, `ai_input_messages`) | 2660–2696 |
| 2 | **Layer A** motorcycle pre-gate → handoff *(can return)* | 2698 |
| 3 | **Layer B** phone-call/human request → escalation *(can return)* | 2702 |
| 4 | **Layer C** explicit unsupported service (F12/transfer/repair) *(can return)* | 2708 |
| 5 | **Layer D** FAQ bypass → `_handle_general_information_ai` *(can return; second AI call site)* | 2714 |
| 6 | **Layer G** vehicle inspectability gate *(can return)* | 2742 |
| 7 | website-form parse (wa.me prefilled) *(can return)* | 2753 |
| 8 | deterministic price re-query *(can return)* | 2766 |
| 9 | deterministic QUOTED acceptance *(can return)* | 2799 |
| 10 | deterministic QUOTED + date proposal → ordered scheduling / `_try_schedule_and_flow` *(can return)* | 2806 |
| 11 | Flow-failure handler *(can return)* | 2847 |
| 12 | deterministic SCHEDULING parse: escalation, period, ordinal, ordered primary/fallback *(can return)* | 2851 |
| 13 | pending fuzzy confirmation handler (acceptance/rejection replay) *(can return)* | 2965 |
| 14 | deterministic catalog lookup `lookup_vehicle(all_recent_text)` → `pre_detected_vehicle` | 3041 |
| 15 | fuzzy lookup on exact miss → CONFIRM question *(can return)* | 3066 |
| 16 | **WILD-02-B** bare numeric model → clarification + pending state *(can return)* | 3106 |
| 17 | **WILD-04-F1** "model del year" → **candidate persisted** (L4.6: evidence-gated) | 3188 |
| 18 | **Layer F** QUALIFYING intent gate *(can return)* | 3225 |
| 19 | role-aware zone detection → `_apply_zone_from_text` (candidate or `state.home_zone_*`) | 3234 |
| 20 | deterministic price quote `_compute_price_quote` → PricingService | 3256 |
| 21 | routing gate + fallback Flow triggers *(can return)* | 3259 |
| 22 | phone-call safety net *(can return)* | 3276 |
| 23 | `resolve_field_evidence()` snapshot → `narrative_needs_ai()` decides prompt richness | 3287–3288 |
| 24 | **`_build_ai_messages()` → `_call_openai()`** — the semantic interpretation call | 3291–3299 |
| 25 | AI output applied: `_apply_candidate`, `_apply_extracted`, `_apply_narrative_interpretation`, `lead_flag` whitelist, price guards | 3300–3520 |
| 26 | post-AI deterministic overrides (quote composition, scheduling, Flow dispatch) | 3520+ |
| 27 | composition finalizers in `_send_text_to_wa`: FAQ reconciliation → `_apply_required_next_question` → **`_enforce_canonical_vehicle_claim`** (L4.6) → `_scrub_invented_price` (applied pre-send on the AI path) | 6041–6060 |
| 28 | `OutboundSafetyGate.attempt(path_id, deployment_id, correlation_id)` → Meta send → `mark_sent/mark_failed` | `outbound_safety_gate.py` |

**22 deterministic layers, 19 possible early-return points, all before the semantic call.**
Vehicle evidence is captured at steps 14–17, location at step 19 — i.e. *after* eighteen
opportunities to return without ever capturing anything.

---

## 2. PART 2 — The semantic interpreter

| | |
|---|---|
| function | `ConversationEngine._call_openai(messages)` (5711), prompt built by `_build_ai_messages()` (5523) |
| file | `backend/app/services/conversation_engine.py` |
| input | system prompt (stage, lead flag, customer name, zone, focus vehicle + id, other candidates, pre-calculated price, deterministically detected vehicle, last 15 DB messages, 20 business rules, optional narrative block) + user turn (single message or ráfaga list) |
| output | one JSON object: `intent`, `reply`, `lead_flag`, `needs_human`, `extracted{…}`, `candidate{…}`, plus (conditionally) 9 narrative fields |
| model | `settings.openai_chat_model`, default `gpt-4o-mini`; `temperature=0.3`, `response_format=json_object`, `max_tokens=1200` |
| called | **CONDITIONALLY** — only if all 19 earlier gates decline |
| per burst | 0 or 1 on the main path (3299); a *different* call at 4568 (`_handle_general_information_ai`, Layer D FAQ path). Never both — Layer D returns. So **≤ 1 semantic call per burst**, but which prompt/schema runs depends on which gate fired. |
| prevented by | any of the 19 early-return gates, `needs_human`, motorcycle, unsupported service, FAQ bypass, website form, deterministic acceptance/scheduling/price-requery paths, fuzzy/numeric clarification |

The richest schema (narrative fields with `CONFIRMED/LIKELY/UNCERTAIN/SUPERSEDED/HISTORICAL/HYPOTHETICAL` statuses) is itself conditional: `narrative_needs_ai()` (narrative_schema.py:127) returns False when vehicle *and* location are already known and no complex marker is present.

---

## 3. PART 3 — Structured semantic output coverage

| Field | Status | Where |
|---|---|---|
| service intent | **PARTIAL** — `intent` enum exists but CE's canonical `last_intent` is set deterministically; AI intent is advisory | `intent` |
| vehicle make | **SUPPORTED** | `candidate.marca`, narrative `vehicle_make_model` |
| vehicle model | **SUPPORTED** | idem |
| vehicle year | **SUPPORTED** | `candidate.anio`, narrative `vehicle_year` |
| vehicle category | **PARTIAL** — AI may propose `tipo_vehiculo`, but catalog authority overrides (WILD-04R-F6) | `candidate.tipo_vehiculo` |
| multiple vehicle mentions | **PARTIAL** — one candidate object per turn; prior candidates can be re-focused by id, but two new vehicles in one burst cannot both be expressed | `candidate` |
| correction vs replacement | **PARTIAL** — narrative `SUPERSEDED` status exists but is only consumed for inspectability; corrections otherwise inferred from `action=update` | narrative |
| inspection location | **PARTIAL** — narrative `vehicle_location` exists; `extracted.zone_detail` is fill-if-absent and DB-normalised | |
| customer origin | **PARTIAL** — narrative `customer_origin` parsed, **never applied** to state | |
| seller location | **NOT_SUPPORTED** | |
| FAQ questions | **PARTIAL** — narrative `asks_faq` / `asks_price` / `asks_schedule` booleans; canonical FAQ answers are deterministic constants | |
| acceptance | **PARTIAL** — via `lead_flag=ACEPTADO`, whitelisted and guarded; deterministic `_is_acceptance` is authoritative | |
| rejection | **NOT_SUPPORTED** as a field (deterministic `_FUZZY_REJECTION_RE` only) | |
| scheduling day | **PARTIAL** — `extracted.preferred_day_iso` accepted fill-if-absent | |
| scheduling time | **PARTIAL** — `extracted.preferred_time_str`, format-validated | |
| **ordered scheduling alternatives** | **NOT_SUPPORTED** — L4.3 solved this deterministically (`_parse_scheduling_requests`) | |
| customer identity | **SUPPORTED** — `extracted.customer_name` (first-write-wins) | |
| seller identity | **PARTIAL** — `extracted.vendedor_tipo` only | |
| address | **NOT_SUPPORTED** — exact address is collected by the Booking Flow | |
| handoff need | **SUPPORTED** — `needs_human` | |
| ambiguity / confidence | **PARTIAL** — only inside the optional narrative block; the main `candidate`/`extracted` objects carry no confidence | |

**Nothing in the schema is unconditionally rich: the confidence-bearing fields live in a block that is only included some of the time.**

---

## 4. PART 4 — Raw-language parsers (inventory)

27 module-level parsers + 13 engine-method detectors. The load-bearing ones:

| Parser | Purpose | Before AI | Can block AI | Can block evidence | Can mutate state | Still necessary |
|---|---|---|---|---|---|---|
| `_is_motorcycle_enquiry` | out-of-scope vehicle | yes | **yes** | **yes** | yes (handoff) | yes — safety |
| `_is_phone_call_request` | human handoff | yes | **yes** | **yes** | yes | yes — safety |
| `_handle_explicit_service_gate` | F12/transfer/repair | yes | **yes** | **yes** | yes | yes — scope |
| `_detect_general_information` (Layer D) | FAQ bypass | yes | **yes** (routes to a second AI call) | **partially** (L4.6 closed the model-del-year hole) | no | as *routing*, yes; as *evidence gate*, no |
| `_detect_prepurchase_signal` / `_detect_explicit_inspection_request` | intent | yes | no (post-L4.6) | **no longer** (L4.6) | yes (`last_intent`) | as routing/tone, yes |
| `lookup_vehicle` | catalog identity | yes | no | no | yes (candidate) | **yes — catalog authority** |
| `extract_model_del_year` / `_contextual_numeric_model_lookup` | numeric models | yes | no | no | yes | yes — deterministic identity |
| fuzzy ASR resolver + `_FUZZY_ACCEPTANCE_RE` / `_FUZZY_REJECTION_RE` | typo/ASR vehicles | yes | **yes** | no | yes | yes — validator |
| `_extract_vehicle_location_zones`, `_extract_zone_from_text`, `_has_customer_origin_clause`, `_strip_customer_origin_clauses` | location roles | yes | no | **no longer** (L4.6) | yes | **hybrid** — DB resolution must stay deterministic; clause understanding is language work |
| `_is_acceptance` / `_has_acceptance_word` | quote acceptance | yes | **yes** | no | yes (flag/stage) | hybrid |
| `_parse_scheduling_requests` / `_parse_scheduling_text` / `_detect_time_period` / `_select_slot_from_offered` | scheduling language | yes | **yes** | no | yes | hybrid — day/time arithmetic deterministic, phrasing is language work |
| `_parse_website_form` | wa.me prefill | yes | **yes** | no | yes | yes — structured input |
| `_is_outside_coverage`, `_has_vehicle_concern`, `_is_generic_vehicle_text`, `_detect_price_question`, `_detect_vehicle_location_phrase` | routing gate inputs | yes | yes | no | no | hybrid |
| inspectability detectors (5) | physical state | yes | **yes** | no | yes | hybrid |

---

## 5. PART 5 — Language gates that control evidence existence

| ID | Raw-language gate | Evidence affected | Can the interpreter bypass it? | Risk | Recommended treatment |
|---|---|---|---|---|---|
| **G-01** | Layer B phone-call pre-gate (2702) | *everything* — "llamame, es por un Focus 2017" escalates without capturing vehicle or zone | **No** — returns before capture | MEDIUM | capture evidence before escalating; escalation is a decision, not a reason to discard facts |
| **G-02** | Layer A motorcycle (2698) | vehicle/zone of the same burst | No | LOW | acceptable (out of scope), but record the evidence for forensics |
| **G-03** | Layer C unsupported service (2708) | all current-turn evidence | No | LOW-MEDIUM | same as G-01 |
| **G-04** | Layer D FAQ bypass (2714) | vehicle evidence when the vehicle is a **fuzzy/typo hit** or a **bare numeric model** — `lookup_vehicle` and (post-L4.6) `extract_model_del_year` are consulted, the fuzzy resolver and `_contextual_numeric_model_lookup` are not | Partially | **MEDIUM-HIGH** — this is the exact class that produced Wild B | move capture before Layer D, or extend the guard to every resolver |
| **G-05** | Layer G inspectability (2742) | zone/vehicle of that turn | No | LOW | capture first |
| **G-06** | QUOTED/SCHEDULING deterministic branches (2799–2963) | zone corrections and vehicle changes stated in the same burst as a scheduling phrase | No | MEDIUM | capture evidence before branch selection |
| **G-07** | fuzzy CONFIRM (3066) and WILD-02-B (3106) | returns before zone detection at 3234 — a location given in the same turn as an ambiguous vehicle is not persisted; it is only replayed later via `pending_turn_evidence_text` | No | MEDIUM | run zone capture before the clarification return |
| **G-08** | `narrative_needs_ai()` (3288) | suppresses the *confidence-bearing* schema when vehicle+location are known | n/a | LOW | make the schema unconditional once evidence is a single object |
| **G-09** | Layer F QUALIFYING intent gate (3225) | may return before zone detection (3234) | No | MEDIUM | capture before gate |

**Nine gates can still suppress or discard evidence; L4.6 closed two specific instances (the intent whitelist and Layer D's numeric-model blindness) but not the pattern.** The pattern is structural: *evidence capture is interleaved with decision-making instead of preceding it.*

---

## 6. PART 6 — FieldEvidence / central resolver

- **EXISTS: PARTIAL** — `backend/app/services/field_evidence.py` (523 lines): `FieldEvidence(value, source, confidence, confirmed, current_turn)` and `FieldEvidenceSnapshot` with 8 fields plus readiness helpers (`vehicle_known`, `location_known`, `pricing_ready`, …). Explicitly **read-only** (ER-10/ER-13: no mutation, no persistence).
- **USED IN LIVE PATH: PARTIAL** — three call sites, all *consumers*, never producers: routing gate (2357), fallback Flow triggers (2514), narrative-necessity check (3287).
- **FIELDS COVERED:** service_intent, vehicle, vehicle_year, vehicle_category, inspection_location, customer_origin, inspectability, scheduling.
- **FIELDS STILL RESOLVED AD-HOC:** acceptance/rejection, scheduling day/time and ordered alternatives, price readiness inputs beyond the snapshot, seller type, customer name, address, FAQ intents, correction-vs-replacement, coverage, phone-call/handoff intent, website-form fields.

**Verdict:** the design was implemented as a *read model* only. It reconciles evidence for
decisions but is not the path through which evidence is created or written — the capture
and the writes remain scattered across ~10 sites in `_process_text`.

---

## 7. PART 7 — AI authority boundary

| Capability | Result | Evidence |
|---|---|---|
| write candidate directly | **PASS** (cannot) — AI proposes `candidate{action,…}`; `_apply_candidate` validates, and catalog authority overrides AI `tipo_vehiculo` (WILD-04R-F6) | 3300+ |
| write location directly | **PASS** — `extracted.zone_detail` is fill-if-absent and only when no DB-validated `home_zone_group` exists; `zone_group` is never read from AI (`_normalize_zone_from_db` owns it) | 6133–6138 |
| write quote / price | **PASS** — prompt rule 8, `PRESUPUESTO_ENVIADO` blocked without a deterministic price, and `_scrub_invented_price` replaces any AI reply containing a price or a quote promise when no deterministic quote exists | 3464–3472, 6588 |
| create booking | **PASS** — the only `ThreadRevision(status="booked")` creator is `_process_flow_response`; `BookingFlowService.handle_confirm_booking` owns the atomic write | audited in L4.3/L4.6 |
| change Lead lifecycle | **PASS** — `lead_flag` must be in `_ALLOWED_FLAGS`, with regression and QUALIFYING→ACEPTADO guards | 3464–3500 |
| create appointment | **PASS** — Revisions are created only by the Flow path, the escalation path (`provisional`, needs_human) and human CRM/API/UI paths | grep of `Revision(` creators |
| bypass PricingService | **PASS** — the only CE quote call is `self._pricing.quote(...)` (6671) | |
| bypass SchedulingService | **PASS** — CE holds one `ScheduleService`; the AI never computes availability | 1627 |
| bypass OutboundSafetyGate | **PASS** — every CE send goes through `gate.attempt()`; `_send_whatsapp_cloud_*` is called only inside gated senders | CLAUDE.md invariant, verified |

**AI DIRECT BUSINESS AUTHORITY: NONE.** This part of the architecture is sound and should
not be loosened by any consolidation work.

---

## 8. PART 8 — Response composition

The AI is **C — both**: it interprets *and* writes the customer-facing `reply` string.

Why Wild B could say "hacemos el servicio de revisión para un 2008 del 2014" with an empty
candidate table: Layer D classified the burst as FAQ-dominant (its vehicle guard could not
see numeric models), routed to `_handle_general_information_ai`, and the AI echoed the
customer's own words. No consistency guard existed between the composed text and canonical
state.

Guards that exist today, all inside `_send_text_to_wa`:

| Guard | Protects |
|---|---|
| `_compose_secondary_answers` / FAQ reconciliation | unanswered FAQs |
| `_apply_required_next_question` | missing location question in QUALIFYING |
| **`_enforce_canonical_vehicle_claim` (L4.6)** | **vehicle** claimed without a candidate |
| `_scrub_invented_price` (AI path) | **price** invented without a deterministic quote |

**Remaining gaps — nothing validates a composed reply against canonical state for:**

| Claim | Guard | Gap |
|---|---|---|
| vehicle | ✅ L4.6 finalizer | closed |
| price | ✅ scrubber | closed (AI path only) |
| **location** | ❌ none | a reply may name a zone that no candidate/state holds |
| **availability** | ❌ none | AI may say "tengo lugar el jueves" without ScheduleService |
| **booking state** | prompt rule 13 only | AI may claim a turno is confirmed; no deterministic guard |
| **acceptance state** | ❌ none | AI may treat the customer as having accepted |

So: **RESPONSE CONSISTENCY = PARTIAL.** The L4.6 finalizer is the only *state-checked*
guard, and it covers exactly one of six claim classes.

---

## 9. PART 9 — One semantic pass per burst

1. **Can the current schema support it?** No. It lacks ordered scheduling alternatives,
   rejection, seller location, address, correction semantics for non-inspectability fields
   and per-field confidence outside the optional narrative block.
2. **Would it require extending the schema?** Yes — one `TurnEvidence` object with, per
   field, `value + status + confidence + span`, and lists where multiplicity is real
   (vehicles, locations by role, scheduling alternatives).
3. **Which deterministic parsers should remain as validators?** catalog lookup
   (`lookup_vehicle`, numeric-model resolvers), zone→DB resolution, pricing, availability,
   business-hours, gate/dedup, date arithmetic, format validation.
4. **Which should stop being primary language interpreters?** intent whitelists,
   acceptance/rejection regex, scheduling phrase parsing, origin/location clause parsing,
   FAQ classification, coverage/concern detectors — these become *confirmatory*, applied to
   the interpreter's spans rather than to raw text.
5. **Which must remain deterministic for safety/performance?** motorcycle scope, phone-call
   handoff, kill switch/gate, dedup, price, availability, booking, cycle reset.
6. **Without giving the LLM DB authority?** Yes — the interpreter returns evidence only;
   a deterministic reconciler decides what becomes canonical. Today's authority boundary
   (Part 7) is exactly the right one and is preserved.
7. **Would it reduce branching?** Substantially: 19 early-return gates collapse toward a
   single UNDERSTAND → RECONCILE → DECIDE pipeline, and the "one more phrasing" class of
   defect disappears.

**FEASIBLE: YES. Scope: MEDIUM** (incremental; the interpreter, the authority boundary and
the read-model already exist — what is missing is a producer-side evidence object and a
reordering so capture precedes decisions).

---

## 10. PART 10 — Domain classification (recommended)

| Domain | Classification |
|---|---|
| natural-language understanding | **SEMANTIC** |
| catalog validation | **DETERMINISTIC** |
| candidate authority | **DETERMINISTIC** (reconciler owns writes) |
| location role reconciliation | **HYBRID** — semantic assigns roles, deterministic resolves zone against DB |
| quote readiness | **DETERMINISTIC** (`FieldEvidenceSnapshot.pricing_ready`) |
| price | **DETERMINISTIC** |
| acceptance | **HYBRID** — semantic proposes, deterministic confirms stage transition |
| scheduling preference interpretation | **HYBRID** — semantic extracts ordered branches, deterministic resolves dates/times |
| availability | **DETERMINISTIC** |
| booking creation | **DETERMINISTIC** |
| response wording | **HYBRID** — semantic composes, deterministic validates against canonical state |

---

## 11. PART 11 — Test architecture gap

Measured pattern across the suites that should have caught Wild A and Wild B:

- **direct helper invocation** — W4F1-01…06 assert `extract_model_del_year("un 2008 del 2014")` directly, bypassing every gate; the helper was always right.
- **whitelisted canonical phrases** — every CE-level fixture uses "Quiero/Quería revisar…", the exact wording the gate required.
- **intermediate assertions** — tests assert parser output, not DB persistence; Wild B failed with a correct parser and an empty table.
- **mocked semantic output** — `_call_openai` is stubbed with a fixed JSON, so no test observes what the interpreter can or cannot express.
- **clean-state fixtures** — L4.2/L4.4 assert the *absence* of inherited state; none drives a real first inbound to persistence.
- **single-phrasing coverage** — one sentence per behaviour, so a phrasing class is never explored.

**Proposed model — semantic-equivalence corpora.** For each invariant, ≥ 20 phrasings that
must all map to the same structured evidence and the same canonical DB result:

| Corpus | Examples of variance |
|---|---|
| inspection intent | "quiero revisar", "para revisar", "necesito una revisión", "me gustaría chequear", "vengo a que me revisen", "estoy por comprar y quiero verlo", bare "revisar un auto" |
| vehicle identity | make+model+year, model-only, numeric model, "del 2014" / "2014" / "año 2014", typos, ASR artefacts, corrections ("no, es un…") |
| location role | subject present/absent, origin before/after, "el auto está en X pero yo vivo en Y", "estoy en X" (ambiguous), bare locality |
| acceptance | "dale", "sí, avancemos", "me sirve", "ok hagámoslo", "listo", "vamos" |
| scheduling alternatives | "mñ 15 o jueves", "mañana a la tarde o el viernes", "hoy no, mañana sí", "cualquier día menos martes" |

Each case asserts **evidence + canonical DB state**, never parser return values. The
production code must not grow a branch per phrase — the corpus is the acceptance criterion
for one general rule.

---

## 12. PART 12 — Architectural verdict

**B — PARTIAL SEMANTIC ARCHITECTURE.**

A semantic layer exists, is well-bounded (it holds no business authority) and produces
structured output. But it runs *last*: 22 deterministic layers and 19 early-return points
decide the turn before it is consulted, canonical evidence is captured by hand-written
parsers at ~10 scattered sites, the central `FieldEvidence` model is a read-model only,
and the interpreter's richest schema is itself conditional. Deterministic pre-gates still
own too much natural-language understanding — which is precisely the shape of the Wild A
and Wild B failures.

(It is not C: the AI is more than a fallback composer — it produces candidate/extracted
structures the reconciler applies. It is not A: nine gates can still suppress evidence.)

---

## 13. PART 13 — Finite remediation roadmap (proposal only — not implemented)

Incremental, no big-bang rewrite. Each phase is independently certifiable and reversible.

| Phase | Content | Risk | Migration strategy |
|---|---|---|---|
| **L4.7A — semantic schema** | Define `TurnEvidence` (value + status + confidence + span per field; lists for vehicles, located roles, scheduling alternatives). Extend the prompt to always return it. No behaviour change: parse and log only, compare against deterministic results. | LOW | shadow mode |
| **L4.7B — single UNDERSTAND pass** | Move the semantic call to the top of `_process_text`, before Layer A. Gates keep deciding, but they consult evidence, not raw text. Latency budget: one call per burst (unchanged count). | MEDIUM | feature flag; shadow-compare decisions for N Wilds before switching |
| **L4.7C — deterministic evidence reconciler** | Promote `field_evidence.py` from read-model to the single write path: reconciler applies TurnEvidence under the L1 authority hierarchy, then persists. Removes the ~10 ad-hoc capture sites. | MEDIUM-HIGH | field-by-field: vehicle → location → scheduling → acceptance |
| **L4.7D — response validator** | Extend the L4.6 finalizer to all six claim classes (vehicle, location, price, availability, booking, acceptance): a composed reply may assert only what canonical state holds. | LOW-MEDIUM | additive |
| **L4.7E — semantic-equivalence corpus** | The corpora of Part 11 as the acceptance criterion; retire phrase-specific fixtures. | LOW | additive |
| **L4.7F — dirty-history + Wild certification** | Re-run L3 dirty-history against the new pipeline, then a controlled Wild. | MEDIUM | gated by L4.7A–E |

Sequencing note: L4.7D and L4.7E are valuable **immediately** and independent of the
pipeline reorder; they should not wait for L4.7B/C.

---

## 14. PART 14 — No-phrase-specific-patch rule (recommended for the roadmap)

> **No production fix may consist solely of adding a phrase, regex or alias so that one
> Wild sentence passes.** Every language fix must implement a documented general semantic
> invariant, and must be accepted by a semantic-equivalence corpus (≥ 20 phrasings mapping
> to the same structured evidence), not by the sentence that triggered it.

L4.6 already complies: it changed *where the gate is* and *what counts as evidence*, and
added no phrase to `_PREPURCHASE_SIGNALS` or `_INSPECTION_REQUEST_PATTERNS`.

---

## 15. Constraints honoured

No code changed · no DB write · no Wild · OUTBOUND OFF · crm_test only ·
L1/L2/L3 remain FROZEN (this audit produces no contradictory runtime evidence against them).
