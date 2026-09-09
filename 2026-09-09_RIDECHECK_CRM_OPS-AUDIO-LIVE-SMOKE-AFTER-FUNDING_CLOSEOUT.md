PROJECT: RIDECHECK_CRM
TYPE: CLOSEOUT
MILESTONE: OPS-AUDIO-LIVE-SMOKE-AFTER-FUNDING

STATUS: PASS
DATE: 2026-09-09
IMAGE: ridecheck-crm-backend:audio-resilience-bb2546d — unchanged, no code modified
SCOPE: crm_test only. Production untouched. Outbound armed for the tester only, then disarmed.

---

## 1. Credit access

Verified from inside the running backend with the configured key, without printing it:

```
inference    : HTTP 200 — credits available
transcription: HTTP 400 invalid_request_error  → billing OK (rejected the empty probe body,
                                                  not the payment)
```

The transcription probe used a deliberately malformed body so nothing was billed. The
contrast with 2026-09-08 is the whole point: the same call then returned
`429 credit_balance_exhausted`.

## 2. The live smoke

One real voice note, on a tester at true zero state — no contact, thread, message, ai_event,
dedup or lock existed beforehand, so it arrived as a genuinely new customer.

```
15:40:22.000  owner voice note sent
15:40:25.994  backend stored inbound        thread 2044, message 6106, type=audio
15:40:26.120  n8n execution 1485 started
      ~:2x    POST /api/whatsapp/media/27635048766174827/transcribe   → HTTP 200
15:40:49.471  CE_DECISION intent_gate       stage=QUALIFYING evidence_capture=True
15:40:49.484  CE_DECISION vehicle_candidate_persisted
                          Peugeot 2008 2014 SUV_4X4_DEPORTIVO (source=model_del_year)
15:40:52.130  CE_SHADOW_UNDERSTAND          ok=True gpt-4o-mini 2668ms tokens=5453 items=2
15:40:52.157  OUTBOUND_GATE_ALLOWED         message_type=flow
15:40:53.778  OUTBOUND_GATE_SENT            message 6107, wamid …NkQ2ODcA=
15:40:53.786  M18 handle                    action=replied latency_ce_ms=4566
15:40:55.359  WHATSAPP_STATUS_PROCESSED     status=sent
15:40:53.805  n8n execution 1485 success
```

**Transcript produced from the real voice note:**

> "Hola, ¿cómo están? ¿Hacen revisiones? Me gustaría revisar un 2008 del 2014."

**Reply delivered to the handset** (`path_id=CE_FLOW`, status `delivered`):

> "Para calcular los viáticos de la revisión, completá dónde está el auto."

The vehicle was resolved *from the transcript* — "un 2008 del 2014" → Peugeot 2008, 2014,
`SUV_4X4_DEPORTIVO` — so the audio genuinely drove the conversation rather than merely
arriving. CE then asked for the location it needs to price viáticos, which is the correct
next step for a qualifying turn with a vehicle but no zone.

## 3. Semantic TurnEvidence — precisely what happened

One model call, successful: `ok=True model=gpt-4o-mini latency_ms=2668 tokens=5453 items=2`.
Exactly one, which satisfies the L4.7C.4A single-flight invariant (`MAX_MODEL_CALLS_PER_BURST
= 1`) — the same interpretation feeds the turn, the claim projection, the reconciler and the
shadow recorder.

Worth stating precisely rather than rounding up: `ai_invoked=f` and
`answer_source=VEHICLE_RESOLVER`. **The semantic call succeeded, but the decision came from
the deterministic vehicle resolver.** That is the intended asymmetric authority — the model
proposes evidence, deterministic code owns the mutation — not a degraded path. The
significance for this milestone is narrower and sufficient: OpenAI is reachable and
answering from inside CE, which is what funding was blocking.

## 4. Duplicate and safety counts

| | |
|---|---|
| CE invocations | **1** (`ai_events` rows for the thread) |
| outbound rows | **1** |
| outbound sent/delivered | **1** |
| outbound blocked | 0 |
| dedup rows | 1 |
| n8n executions | 1 (id 1485, success) |

No duplicate CE invocation, no duplicate outbound, no retry storm.

## 5. Latency

| segment | ms |
|---|---|
| inbound stored → reply delivered | **26,123** |
| of which n8n debounce | ~20,000 (by design) |
| CE compute (`latency_ce_ms`) | 4,566 |
| semantic model call | 2,668 |
| n8n execution wall | 27,685 |
| `latency_total_ms` recorded by CE | 26,122 |

Net of the deliberate 20-second debounce, the system took roughly 6 seconds from burst close
to a delivered reply, with the model call the largest component.

## 6. Post-smoke state

- **Outbound OFF** — backend recreated without `BETA_OUTBOUND_ENABLED`; verified
  `OUTBOUND_ENABLED=false`
- **Tester reset to zero state** — contacts, threads, messages, ai_events, leads, dedup all 0
- **Agenda intact** — 14 appointments in the 07–13 September week
- **No code changed**; image still `audio-resilience-bb2546d`

Evidence preserved before the reset:

```
/opt/ridecheck-crm-forensics/OPS-AUDIO-LIVE-SMOKE_20260909T154244Z.tar.gz
sha256 47a7306dc8651f213f6402bf274c94b076d43d9cf6a227a9ec16636a90e05a33
```

## 7. What this closes

The voice path is now proven end to end on real hardware with a real voice note:
WhatsApp → backend → n8n → Transcribe Audio → real transcript → CE → semantic evidence →
response → OutboundSafetyGate → delivered. The failure that silently lost three voice notes
on 2026-09-08 was an unfunded OpenAI balance, and it is resolved.

The retry and classification logic added in `bb2546d` was **not** exercised here — the
upstream succeeded on the first attempt, which is the desired outcome. It remains unit-proven
only, and that is the honest status: it will earn live evidence the next time an upstream
actually wavers, not before.
