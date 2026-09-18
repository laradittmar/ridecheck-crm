PROJECT: RIDECHECK_CRM
TYPE: AUDIT
MILESTONE: L4.7W5-HYBRID-DECISION-INSPECTOR-AUDIT

Roadmap adopted: `docs/Launch/2026-09-18_..._PRELAUNCH-CANONICAL-ROADMAP_AUDIT_001.md`. Gates 1 and 2 audited and designed. **Nothing implemented.**

**The dashboard screenshot referenced in the prompt did not arrive.** The `/control` audit below is from executable source (`ui/control_view.py`, `routes/ops_dashboard.py`) and the live route table, not from an image. Send it and I will reconcile the visual against the code.

## Executive finding

**The evidence is largely being captured already. It is scattered across three uncorrelated files, keyed by nothing stronger than a timestamp, and invisible to the dashboard.** Gate 1 is therefore far smaller than "build evidence capture" — it is *correlate and complete what exists*. Three durable append-only streams survive container recreates on the `/opt/ridecheck-crm-forensics` bind mount:

```
shadow_turn_evidence.jsonl    92 records   semantic evidence + shadow reconciliation   HAS burst_id + correlation_id
reconciliation_records.jsonl  33 records   LIVE reconciler decisions                   thread_id + timestamp ONLY
authorization_records.jsonl   37 records   acceptance / scheduling authorization       thread_id + timestamp ONLY
```

Two corrections to prior conclusions, both from executable source:

1. **Both engines demonstrably receive the identical complete burst.** `_run_shadow_understand(ctx, event, ai_input_messages)` (`conversation_engine.py:3386`) passes the *same variable* the router uses. There is no reduction to the final message anywhere in the CE path. The roadmap's "forensic record cannot prove which complete burst the model saw" is right about the **record** and wrong about the **code**: the model did receive both 6198 and 6199. My F7H audit under-claimed here; the source settles it.
2. **All four reconciler authority flags are LIVE in the deployed runtime**, not shadow: `reconciler_vehicle_authority_enabled`, `_location_`, `_acceptance_`, `_scheduling_` all `True`, alongside `shadow_understand_enabled`, `shadow_understand_async` and `semantic_same_turn_enabled`.

The real gap is narrower and sharper than "no observability": **for the failed Wild turn the persisted trace is semantic-only.** There is no reconciliation record, no authorization record and no CE evidence record for 20:28:5x — because rejection/rescue is a claim family that **bypasses reconciliation entirely**. The absence of a decision is itself unrecorded, which is exactly the condition a decision inspector must make visible.

## Current executable topology

```
WhatsApp → Meta webhook → nginx → POST /integrations/whatsapp/webhook   (HMAC fail-closed)
                                     ↓ persist inbound, allowlist screen (routes/whatsapp.py:262)
                                  n8n  "CRM - Ridecheck" (DaFqDIzVi1f92Hvz, ACTIVE)
                                     ├─ Transcribe Audio → backend /api/whatsapp/media/{id}/transcribe
                                     ├─ Describe Image   → gpt-4o
                                     ├─ Wait (20 sec)    ← the ONLY debounce
                                     ├─ Build Conversation Context (code node)
                                     └─ Call Backend Engine (M18) → POST /api/conversation/handle
                                          { thread_id, wa_message_id, recent_user_messages,
                                            unanswered_recent_user_messages, … }
                                     ↓
ConversationEngine.handle → _handle → _process_text
   ai_input_messages = event.unanswered_recent_user_messages  (else _current_evidence)
        ├──────────────► _run_shadow_understand(ctx, event, ai_input_messages)  → semantic
        └──────────────► deterministic predicates, guards, floors               → CE
   → field_reconciler / acceptance_authorizer / scheduling_reconciler  (LIVE)
   → canonical state, OutboundSafetyGate, response
```

`/control` is served by `app.ui.kanban` rendering `ui/control_view.py` (54,836 bytes of server-rendered Python f-strings), fed by six JSON endpoints in `routes/ops_dashboard.py` (783 lines): `/api/ops/summary`, `/path-registry`, `/messages`, `/threads`, `/paths`, `/critical-events`. Auth is the signed `crm_session` cookie; `_mask_wa_id` masks numbers; `_preview` truncates text.

## Failed-Wild five-layer trace (thread 2053, 20:28)

| Layer | What is persisted today | Verdict |
|---|---|---|
| **RawEvidence** | `whatsapp_messages` 6198 `Mmm, no me sirve` 20:28:27, 6199 `No tenes algo más temprano ?` 20:28:35 | **complete** |
| **Semantic** | `shadow_turn_evidence.jsonl` burst `7215c5cf`, 20:28:57, `ok=true`, `gpt-4o-mini`, `understand/1.18`, `turn-evidence/1.2`, deployment `ce4b797`, context keys incl. `offered_slots`; evidence: `acceptance:null handoff:null scheduling_requests:[] ambiguities:[] conflicts:[]`; **`message_ids` = one WAMID (6199's)** | **captured, input boundary unprovable** |
| **Deterministic CE** | *nothing* — `_earliest_option_rejected=True`, `_rejection_is_about_scheduling=False`, `_rejects_every_offered_option=False`, `_should_escalate=False` are transient booleans | **NOT PERSISTED** |
| **Reconciliation** | *no record at all* for this turn. Only the shadow reconciler ran (`reconciler:shadow:v1`, `shadow:true`), over carried-over identity claims | **NOT PERSISTED / BYPASSED** |
| **Canonical action** | outbound row 6200 + text; no state change, no reason code, no "why nothing happened" | **outcome only, no justification** |

For contrast, the three earlier turns **do** have live records: `inspection_location ACCEPT` and `vehicle.model HOLD→ACCEPT` at 20:25, `authorize.quote_acceptance` at 20:26:14, `vehicle.model HOLD` + `authorize.scheduling_progression` at 20:27:24. The pipeline records decisions it *makes*; it records nothing when no rule owns the turn.

## Burst-integrity findings

| # | Finding | Evidence |
|---|---|---|
| A1 | Debounce is **n8n's `Wait (20 sec)`** node — the only burst grouping in the system. CE performs none. | workflow `RUNTIME_LIVE_EXPORT_2026-09-04.json` |
| A2 | Burst text reaches CE as `unanswered_recent_user_messages`, built by n8n's `Build Conversation Context` code node from `Get Thread Messages 2` | `schemas/conversation.py:49-51` |
| A3 | **Both engines receive the same list object** — no divergence is structurally possible | `conversation_engine.py:3370, 3386` |
| A4 | **Message boundaries survive into CE** and are load-bearing: F7G-R2 derives clauses per message | `_rejection_is_about_scheduling` |
| A5 | **`message_ids` records only `event.wa_message_id`** — one triggering WAMID, never the burst | `conversation_engine.py:5072-5074` |
| A6 | `TurnRef.ordered_message_ids` is typed as a tuple but is **structurally single-valued in the live path** | `schemas/turn_evidence.py:160` |
| A7 | **No burst text and no input hash are stored** (`sanitized_items: 0`, by privacy design) | shadow record |
| A8 | Messages arriving *during* processing land in the next debounce window; there is no in-flight merge | n8n Wait semantics |
| A9 | Duplicates are caught by `state.last_processed_inbound_wa_message_id` → `skipped_dedup`, and by causal dedup in the outbound gate | `_handle` L2072-2077 |
| **A10** | **No place in CE reduces a burst to its last message.** The reduction is purely in the *record*. | source-wide trace |

**What must change to prove the input boundary:** persist `ordered_message_ids` as the burst's full ordered WAMID list, plus a privacy-safe `input_hash` (SHA-256 of the normalized joined burst) and `message_count`. Two fields and one list. No raw text needs to be added to the semantic record for this.

## Semantic claim / consumer inventory

| Claim | Producer | Live consumer | Can affect canonical state? | Authority rule | Absent / conflict behaviour |
|---|---|---|---|---|---|
| `vehicle.make/model/year/category` | interpreter + deterministic parser | `field_reconciler.reconcile_vehicle_identity` | **Yes** | `reconcile.vehicle_identity` — **catalog** decides, neither producer wins | HOLD, then deterministic-only |
| `inspection_location` (+role) | both | `field_reconciler.reconcile_inspection_location` | **Yes** | `reconcile.inspection_location`, zone-validated | HOLD; origin never becomes inspection location |
| `quote_accepted` (ACCEPT / NEGATED) | `acceptance` signal | `acceptance_authorizer` | **Yes** | `authorize.quote_acceptance` | REJECT/HESITATE/FUTURE **block**, never average |
| `future_intent`, `searching_not_ready` | acceptance signal | acceptance authorizer | blocks only | same | — |
| `scheduling_preference` | `scheduling_requests` | `scheduling_reconciler` → ScheduleService | **Yes** (proposal only) | `authorize.scheduling_progression` | deterministic parse alone |
| `needs_human` | `handoff.requested` | **`_semantic_handoff_requested`** (F7C) + shadow | **Yes** — the only semantic→rescue route | none; `UNRESOLVED_STATUSES` gate + deterministic floor first | floor only |
| `faq_topic`, `service_intent`, `inspectability`, `correction` | interpreter | shadow reconciler; FAQ authority partially live | mostly no | `reconcile.*` (shadow) | — |
| **rejection of the offered appointment set** | **none** | **none** | **no** | **none** | **the gap** |
| **request for a different/earlier option** | **none** (`SchedulingRequestEvidence` can say *"tomorrow"*, never *"earlier than what you offered"*) | **none** | **no** | **none** | **F-03** |

Failure handling: timeout/HTTP error/malformed JSON → `ok=false`, evidence dropped, turn unaffected; `_semantic_turn_evidence()` returns `None`; every consumer degrades to deterministic-only. Latency, prompt/completion/total tokens, dispatch mode and error are already recorded.

## CE evidence inventory

| Kind | Examples | Structured today? |
|---|---|---|
| Extracted facts | `VehicleMatch(marca, modelo, tipo_vehiculo, confidence, matched_alias)`, `LocalityMatch`, zone group/detail | **Yes** — dataclasses, and they reach reconciliation |
| Parser results | `_parse_scheduling_text → (day, time)`, `extract_model_del_year` | Yes in-memory, **not persisted** |
| Predicates | `_earliest_option_rejected`, `_rejection_is_about_scheduling`, `_rejects_every_offered_option`, `_is_human_request`, `_is_phone_call_request`, `_should_escalate_scheduling_to_human`, `_is_outside_coverage` | **No — transient booleans** |
| Safety floors / guards | F7C–F7G-R2 chain, `_offer_outstanding`, `_turn_took_an_option`, `_opens_a_condition` | **No** |
| Business-service results | `ScheduleService.check/list_slots`, `PricingService` quote | partially, via `CE_RESPONSE_VALIDATION` log lines |
| CE recommendation | `ConversationHandleOut.action` + `detail` | **Yes**, returned to n8n; not stored as decision evidence |

**Minimal set worth persisting** — *not* every variable: the named predicates that gate a transition, each as `{rule_id, rule_version, value, inputs_digest}`; the business-service verdict actually used; and the final CE recommendation with its reason code. That explains the decision without exposing internals: the dashboard binds to **rule identifiers**, never to Python symbol names.

## Reconciliation authority matrix

| Claim family | Semantic in | CE in | Agreement | Disagreement | Semantic-only | CE-only | Semantic absent | Persisted? |
|---|---|---|---|---|---|---|---|---|
| vehicle identity | yes | yes | ACCEPT | **catalog arbitrates** | HOLD→catalog | ACCEPT | CE-only | **yes** (`reconciliation_records.jsonl`) |
| inspection location | yes | yes | ACCEPT | zone validator arbitrates | HOLD | ACCEPT | CE-only | **yes** |
| quote acceptance | yes | yes | ACCEPT | REJECT/HESITATE block | authorizer decides | deterministic lexicon | floor | **yes** (`authorization_records.jsonl`) |
| scheduling preference | yes | yes | reconciler picks | rule decides, not recency | semantic branch | deterministic parse | deterministic | **yes** |
| needs_human (F7C) | yes | n/a | — | — | escalates if CONFIRMED | floors escalate | floor | **no** |
| **offered-option rejection** | **none** | F7D-R2 / F7E / F7G-R2 floors | **n/a** | **n/a** | **n/a** | floor only | floor only | **NO** |

**Bypassing reconciliation entirely:** offered-option rejection, human rescue, phone-call/human-request detection, coverage, inspectability gating, motorcycle handoff. Each is CE-only, each can mutate canonical state through `_handle_scheduling_escalation` or a gate, and none produces a reconciliation record.

**The gap, stated without designing a patch:** there is no evidence claim whose referent is *the offered appointment set*, and no authority rule for it. `QUOTE_ACCEPTED/NEGATED` refers to **the quote**; using it for appointment rejection would conflate two business meanings and is explicitly out of scope. Gate 3 owns the decision; Gate 1/2 must only make the absence **visible**.

## Persistence gap analysis

| Needed | Exists | Where | Gap |
|---|---|---|---|
| ordered inbound IDs + timestamps | partial | `whatsapp_messages` | not linked to a turn |
| normalized burst + input hash | **no** | — | **new** |
| semantic versions, latency, tokens, error | yes | shadow JSONL | — |
| semantic structured evidence | yes | shadow JSONL | — |
| CE evidence | **no** | — | **new** |
| canonical state before/after | **no** | — | **new** |
| reconciliation inputs/rule/reason | partial | reconciliation + authorization JSONL | **no `burst_id`/`correlation_id`** |
| agreement / conflict / missing classification | shadow only | shadow JSONL | not for live decisions |
| allowed/blocked transition + reason | **no** | — | **new** |
| response plan + outbound result | partial | `whatsapp_messages` (`path_id`, `status`, `blocked_reason`) | not linked to a turn |
| deployment SHA | yes | all three + ledger | — |
| **one correlation key across all of it** | **NO** | — | **the central defect** |

## Proposed Hybrid Decision Trace contract (`hybrid-decision-trace/1.0`)

One record per turn, keyed by `turn_id` (reuse the existing `correlation_id`/`burst_id` — already a UUID minted per burst).

| Field | Purpose | Source of truth | Sensitivity | PII | Masking | Retention | Index |
|---|---|---|---|---|---|---|---|
| `turn_id` | join key for all layers | CE `_correlation_id` | low | no | — | 180 d | **PK/idx** |
| `thread_id`, `lead_id` | navigation | DB | low | no | — | 180 d | idx |
| `deployment_sha` | attribution | `GIT_SHA` | low | no | — | 180 d | idx |
| `recorded_at`, `turn_started_at` | ordering, latency | CE | low | no | — | 180 d | idx |
| `ordered_message_ids[]` | **proves the input boundary** | event/burst | low | no | — | 180 d | — |
| `message_count` | burst size at a glance | derived | low | no | — | 180 d | — |
| `input_hash` | proves *which* text, without storing it | SHA-256 of normalized burst | low | no | — | 180 d | idx |
| `normalized_burst` | operator readability | CE `_norm_lower` | **medium** | **yes** | operator-auth only, never in list views | **90 d** | — |
| `semantic{model,prompt_version,schema_version,dispatch,latency_ms,tokens,ok,error_category}` | model accountability | shadow record | low | no | — | 180 d | idx on `ok`,`error_category` |
| `semantic_evidence` | structured claims | `TurnEvidence.to_dict()` | medium | possible (localities/names) | as today | 180 d | — |
| `ce_evidence[]` `{rule_id, rule_version, value, inputs_digest}` | **new** — explains CE | CE predicates | low | no | — | 180 d | idx on `rule_id` |
| `canonical_before` / `canonical_after` `{stage, needs_human, zone, candidate_id, revision_id, offer_outstanding}` | what changed | ThreadState | low | no | — | 180 d | — |
| `offer_ref` `{active_date, offered_slots[], token_present:bool, token_fingerprint}` | offer context **without the token** | state | medium | no | **never the token**, 8-char fingerprint only | 180 d | — |
| `reconciliation[]` `{claim_type, semantic_in, ce_in, classification, rule_id, rule_version, outcome, accepted[], rejected[], reason_code}` | the decision | reconcilers | low | no | — | 180 d | idx on `claim_type`,`classification` |
| `decision{transition, allowed, blocked_reason, reason_code}` | allowed/blocked | CE | low | no | — | 180 d | idx |
| `response_plan{kind, source, validator_actions[]}` | why this reply | CE + L4.7D validator | low | no | — | 180 d | — |
| `outbound{message_id, path_id, gate_outcome, wamid_tail, status}` | delivery | ledger | medium | no | tail only | 180 d | idx |

**Never stored:** secrets, complete booking tokens, credentials, raw model chain-of-thought (never requested either), full phone numbers, duplicated message bodies already in `whatsapp_messages`.

`classification ∈ {AGREE, CONFLICT, SEMANTIC_MISSING, SEMANTIC_ERROR, CE_MISSING, NO_RULE}` — **`NO_RULE` is the one that would have made the Wild failure legible**, and it must be emitted when a turn produces evidence no authority owns.

**Storage choice:** a single `hybrid_decision_traces` table with a JSONB payload plus the indexed scalar columns above. JSONL continues in parallel during the vertical slice as a fallback, then becomes redundant.

## Dashboard information architecture

**Extend `/control`; do not fork it.** The existing dashboard is an operational overview and stays. Add:

```
GET /control/turn/{turn_id}          server-rendered drill-down (ui/control_view.py sibling module)
GET /api/ops/turn/{turn_id}          full trace JSON
GET /api/ops/turns?filters…          list/filter
```
plus a link column from the existing message trace → the turn.

**Default readable view**

1. **Conversation timeline** — grouped inbound burst (all message IDs, not one), outbound response, stage and canonical state per turn, outstanding offer, Flow/token lifecycle (fingerprint only), human ownership, deployment attribution.
2. **Five panels** — RawEvidence · Semantic engine · Deterministic CE · Reconciler · Canonical result, exactly as the roadmap's §Gate 2 table specifies.
3. **Badges** — `AGREE` `CONFLICT` `SEMANTIC MISSING` `SEMANTIC ERROR` `CE MISSING` `BLOCKED` `CLARIFICATION` `HANDOFF` `DETERMINISTIC FLOOR` `FALLBACK`. `DETERMINISTIC FLOOR` is non-negotiable: it is what stops a floor being mistaken for hybrid capability on screen.
4. **Filters** — deployment, claim type, conflict type, semantic missing/error, deterministic floor, handoff, blocked transition, false progression, latency range.
5. **Expert detail** — collapsed structured JSON, version identifiers, correlation IDs, reason codes; no secret values; phone masking via the existing `_mask_wa_id`.

The failed Wild turn would render as: RawEvidence 2 messages · Semantic `ok`, no stance · CE `_earliest_option_rejected=True`, `_rejection_is_about_scheduling=False` · Reconciler **`NO_RULE`** · Canonical **no transition**, response `AI_FALLBACK` — badges `SEMANTIC MISSING` + `NO_RULE` + `FALLBACK`. One screen, no SQL.

## Privacy and retention

Raw customer text may be shown to authorized operators — it already exists in the WhatsApp thread they can open — but `normalized_burst` gets the **shortest retention (90 d)**, is excluded from list views and CSV export, and never appears in logs. Everything else is decision metadata at 180 d. Phone numbers stay masked to the existing CRM policy. Booking tokens are replaced by an 8-character fingerprint. Model internals beyond structured evidence are neither requested nor stored. A documented purge job must exist before Gate 5 ingests real client data.

## Finite implementation milestones

| # | Milestone | Content | Gate |
|---|---|---|---|
| **G1.1** | `HYBRID-TRACE-BURST-INTEGRITY` | **Smallest possible, highest value.** Record `ordered_message_ids[]`, `message_count`, `input_hash` in the shadow record. No table, no UI. Closes A5–A7 and Gate 1's "input boundary is provable". | 1 |
| **G1.2** | `HYBRID-TRACE-CORRELATION` | Add `turn_id`/`correlation_id` to `reconciliation_records` and `authorization_records`. Three streams become joinable. Still no table. | 1 |
| **G1.3** | `HYBRID-TRACE-CONTRACT` | Schema + migration for `hybrid_decision_traces`; writer assembles the envelope; `NO_RULE` classification emitted. | 1 |
| **G1.4** | `HYBRID-TRACE-CE-ADAPTER` | CE predicates report `{rule_id, rule_version, value}` through one adapter. No behaviour change — a test asserts identical routing before/after. | 1 |
| **G2.1** | `HYBRID-INSPECTOR-VERTICAL-SLICE` | `/api/ops/turn/{id}` + `/control/turn/{id}` rendering all five panels and badges **for one real turn end to end** — the failed Wild turn is the fixture. | 2 |
| **G2.2** | `HYBRID-INSPECTOR-FILTERS` | list view, filters, links from the message trace. | 2 |
| **G2.3** | `HYBRID-INSPECTOR-EXPERT-DETAIL` | JSON expansion, retention/purge job, export controls. | 2 |

Each: targeted tests → full regression against the then-current baseline → clean commit-only build → pin → backend-only recreate → read-only certification. **Backfill: none.** Older conversations lack the evidence; synthesising it would fabricate history. The inspector shows `TRACE NOT CAPTURED (pre-G1.3)` for them. **Rollback** for G1.1–G1.2 is a revert (append-only writes, no reader depends on them yet); for G1.3+ the table is additive and the writer is behind a flag, so rollback is flag-off plus image revert; the migration is not dropped.

## Risks and non-goals

| Risk | Severity | Mitigation |
|---|---|---|
| Trace writer adds latency to the customer turn | **high** | write async/after-response like the shadow recorder; failure degrades to "no trace", never to a failed turn |
| Storing `normalized_burst` widens PII exposure | medium | 90-day retention, operator-auth only, excluded from lists/exports, purge job before Gate 5 |
| CE adapter changes routing by accident | **high** | pure reporting; a test asserts byte-identical decisions before/after |
| Dashboard couples to Python internals | medium | bind to `rule_id`/`rule_version` strings only |
| Trace volume growth | low | ~one row per turn; indexed scalars, JSONB payload |
| Observability mistaken for capability | **high** | the `DETERMINISTIC FLOOR` badge and an explicit "hybrid gap open" banner until Gate 3 closes |

**Non-goals, explicitly:** no semantic prompt/schema/claim change; **no offered-appointment-rejection capability** (Gate 3 — and it must stay separate precisely so the inspector can reveal the gap honestly); no F-03 alternative-search behaviour (Gate 6); no Smart Booking; no new claim type; no reuse of `QUOTE_ACCEPTED/NEGATED`; no backfill; no cleanup of unrelated probe files.

## Required conclusions

1. **Same complete burst?** **Yes** — proven by source: one `ai_input_messages` list reaches both engines.
2. **Can current records prove the exact semantic input?** **No** — one WAMID, no burst text, no hash.
3. **Is CE output explainably structured?** **Partially** — vehicle/location/acceptance/scheduling yes; every rejection, rescue, coverage and guard predicate is a transient boolean.
4. **Which reconciliation decisions are persisted?** vehicle identity and inspection location (`reconciliation_records.jsonl`), quote acceptance and scheduling progression (`authorization_records.jsonl`), shadow reconciliation (`shadow_turn_evidence.jsonl`). **None for rejection or rescue**, and none carries a correlation key.
5. **Minimum new persistence contract:** `hybrid-decision-trace/1.0` above, keyed by `turn_id`, with `NO_RULE` as a first-class classification.
6. **Extend or fork the dashboard?** **Extend** — `/control` stays the overview; add `/control/turn/{turn_id}` and two API endpoints. No rewrite.
7. **Smallest vertical slice:** **G1.1** (burst integrity, three fields, no migration) then **G2.1** (one turn rendered end to end). G1.1 alone closes the single finding that made the Wild un-diagnosable.
8. **Risk:** migration **low** (additive table, flagged writer, no backfill); operational **medium** — concentrated in write-path latency and PII retention, both mitigated above.
9. **Recommended next implementation milestone: `L4.7W5-G1.1-HYBRID-TRACE-BURST-INTEGRITY`** — record the burst's full ordered WAMIDs, count and input hash. Smallest change with the largest evidentiary return, no schema change, trivially revertible.
10. **Before the next Wild replay:** G1.1 → G1.2 → G1.3 → G1.4 → G2.1 must land and be deployed, so the replay is auditable live rather than reconstructed afterwards. That is the roadmap's §7 ordering and this audit found nothing contradicting it.

## Confirmation

Nothing was changed, created, committed, pushed, built or deployed. No code, template, JavaScript, CSS, schema or migration was modified. No outbound, no tester reset, no Wild, no WhatsApp message or Flow, **no Meta call of any kind in this milestone**, no n8n change, no database mutation, no Agenda change. Semantic prompts, claims, reconciliation rules and canonical behaviour are untouched. Runtime remains `w5f7g-ce978a2`, container `6c9db45bf53d`, `GIT_SHA=ce978a2`, `OUTBOUND_ENABLED=false`, database `crm_test`. Evidence came from executable source, the three persisted forensic streams, the live route table and read-only database queries.
