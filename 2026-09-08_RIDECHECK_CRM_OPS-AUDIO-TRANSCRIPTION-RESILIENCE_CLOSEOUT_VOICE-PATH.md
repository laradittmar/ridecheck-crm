PROJECT: RIDECHECK_CRM
TYPE: CLOSEOUT
MILESTONE: OPS-AUDIO-TRANSCRIPTION-RESILIENCE

STATUS: CONDITIONAL_PASS
DATE: 2026-09-08
CODE COMMIT: bb2546d
IMAGE: ridecheck-crm-backend:audio-resilience-bb2546d
SCOPE: crm_test only. Production untouched. OUTBOUND OFF throughout — never armed.
BLOCKER: Part 4 cannot be executed. The OpenAI account has no credits. Owner action required.

---

## 1. Root cause — recovered exactly, not inferred

The n8n execution data for 1478/1479/1480 was still on disk. Read with the WAL present, it
gives the upstream body verbatim:

```
OpenAI transcription failed: HTTP 429: {
    "error": {
        "message": "You have no credits remaining. Add credits to continue using the API...",
        "type": "insufficient_quota",
        "code": "credit_balance_exhausted"
    }
}
```

**Not a 502. Not transient. Not an n8n problem.** Verified still true from inside the running
backend using the configured key:

```
inference call: HTTP 429 type=insufficient_quota code=credit_balance_exhausted
```

Every voice note will fail identically until credits are added. No retry policy can change
that, which is why this milestone's central request — prove the real audio path — cannot be
satisfied today.

## 2. Part 1 — Transcribe Audio node audit

The node is **not** an OpenAI node. It calls our own backend:

| | |
|---|---|
| node type | `n8n-nodes-base.httpRequest`, typeVersion 4.4 |
| URL | `POST http://backend:8000/api/whatsapp/media/{{media_id}}/transcribe?wa_message_id={{…}}` |
| credentials | none attached (the key lives in the backend, not in n8n) |
| options | **`{}`** — no timeout, no retry, no `continueOnFail`, no error branch |
| on 5xx | default `stopWorkflow` — the execution ends |
| CE on failure | **receives nothing at all** — the workflow dies upstream of the CE node |

Behind it, `backend/app/api/whatsapp.py` downloads the Meta media and posts it to
`https://api.openai.com/v1/audio/transcriptions` (`whisper-1`, language `es`, 60 s timeout).
Before this milestone every failure — HTTP error, transport error, malformed JSON, empty
transcript — was collapsed into a bare `HTTPException(502)`.

That collapse is the reason the first investigation went after a non-existent n8n wedge: a
429 with an exhausted balance and a genuine gateway blip were indistinguishable.

## 3. Parts 2 & 3 — what was implemented

**Bounded retry with honest classification** (`_classify_transcription_error`):

- retried: HTTP 5xx, transport/URLError, and 429 that is *actually* rate limiting
- **not** retried: `insufficient_quota`, `credit_balance_exhausted`, `invalid_api_key`,
  `account_deactivated`, `billing_hard_limit_reached`, 401/403
- 3 attempts maximum, backoff 0.5 s then 1.5 s

A 429 is ambiguous and is now *read* rather than assumed — real rate limiting clears on its
own, an unpaid balance never does, and retrying the latter only makes the customer wait
longer for the same failure.

The upstream status and error code now survive into the error detail
(`HTTP 429 [permanent/credit_balance_exhausted]: …`) and into structured
`TRANSCRIPTION_RETRY` / `TRANSCRIPTION_FAILED` log lines. No transcript is ever fabricated —
an empty upstream transcript is still a failure — and no alternative provider is contacted.

**Stalled-transport detection** (`_check_forwarded_but_unfinished`, threshold 180 s):

The existing `unanswered_alert` could not have caught this incident. Its query requires
`reply_required = true AND alert_eligible = true`, and both flags are written **by CE**.
Because CE was never invoked, the three events stayed:

```
134/135/136  status='triggered'  reply_required=NULL  alert_eligible=NULL
```

permanently invisible. The new check keys on `status='triggered'` — meaning the backend
forwarded to n8n and nothing ever came back — inside the same loop, reusing the same
`unanswered_alert_sent_at` de-duplication. No new table, process or threshold machinery. It
logs `thread_id`, `wa_message_id` and stage, never message content.

## 4. Part 3 proven live

An audio probe was sent through the real webhook; its execution stalled exactly as the
owner's messages did, and the alert fired on its own:

```
16:01:41 ERROR STALLED_TRANSPORT event_id=139 thread_id=2043
                wa_message_id=wamid.OPSAUDIO.AUDIO.20260908A
                stage=forwarded_to_n8n_no_ce_completion age_threshold=180s
16:01:41 ERROR STALLED_TRANSPORT_SUMMARY count=1 event_ids=[139]
16:02:41 INFO  unanswered_alert wake-up: event_candidates=none      ← de-duplicated
```

~184 seconds from message to alert. On 2026-09-08 the same class of failure was found only
because the owner asked why nobody had answered.

## 5. Part 4 — REAL AUDIO SMOKE NOT PERFORMED

**I did not arm outbound and I am not asking for a voice note.** With the balance at zero,
a voice note would fail deterministically at the same node and waste the owner's time. This
is the same mistake I made when I reported "READY FOR COMPLETE OWNER WILD: YES" without
checking the prerequisite; I am not repeating it.

What was proven live instead, on the deployed image:

| path | result |
|---|---|
| text: webhook → n8n → CE | **PASS** — `M18 handle thread_id=2043 action=service_gate_blocked` |
| audio: webhook → n8n → transcription | fails, as expected, before CE |
| stalled-transport alert | **PASS** (§4) |

One honest gap: the audio probe used a synthetic media id, so it failed at the Meta media
download *before* reaching the retry loop. **The retry code is proven by unit test, not
live.** Exercising it live needs a real Meta media id and a funded account — both of which
are exactly what Part 4 is blocked on.

## 6. Part 5 — failure-case tests

`tests/test_ops_audio_transcription_resilience.py`, 17 tests, all mocked — no real outage
forced:

| id | assertion |
|---|---|
| AUDIO-01 | transient 5xx retries once, then succeeds |
| AUDIO-02 | retry bounded at exactly 3 attempts |
| AUDIO-03 | exhausted credit fails on attempt 1 — no retry |
| AUDIO-04 | invalid key fails on attempt 1 |
| AUDIO-05 | transport error retries and recovers |
| AUDIO-06 | retry loop contains no CE call, no gate, no send — no duplicate CE, no duplicate outbound |
| AUDIO-07 | no transcript invented; empty transcript is a failure |
| AUDIO-08 | upstream status and code survive to the caller |
| ALERT-01/02/03 | detection wired, keyed on `status='triggered'`, must not depend on the CE-written flags, surfaces identifiers not content |
| WAL-01/02 | rule documented; helper copies all companion files |

**Regression: 3735 passed / 57 failed / 9 errors** — identical to the W4-F3 baseline,
**0 new**, +17 passing.

## 7. Part 6 — WAL forensic rule

`docs/operations/N8N_FORENSIC_READ_RULE.md` records the invariant and, deliberately, what it
cost: a WAL-less copy showed `1475 running` / `1476 new` and no newer executions, producing a
confident wrong diagnosis and an unnecessary restart. With the WAL, both had succeeded and
the real failure was in the execution data all along.

`scripts/n8n_forensic_copy.sh` copies `database.sqlite`, `-wal`, `-shm` and the event log
together, and warns that execution status is UNVERIFIED if a companion file is missing. The
doc directs operators to read `n8nEventLog*.log` first — append-only, always current, and the
artifact that actually identified the failing node.

## 8. Gate

| criterion | result |
|---|---|
| REAL VOICE NOTE → CE | **NOT RUN — blocked, no OpenAI credits** |
| TRANSCRIPTION 5xx RETRY | PASS (unit) |
| RETRY DUPLICATE CE | 0 |
| RETRY DUPLICATE OUTBOUND | 0 |
| SILENT TRANSCRIPTION FAILURE | 0 — alert proven live |
| FORWARDED-BUT-UNFINISHED ALERT | PASS (live) |
| TEXT PATH REGRESSION | 0 — CE reached live |
| NEW LAUNCH FAILURES | 0 |
| UNKNOWN BLOCKER/HIGH | 0 — the blocker is known and external |

## 9. Owner action required

Add credits at `https://platform.openai.com/settings/organization/billing/`. Then the voice
smoke takes one message and about two minutes to confirm.

Worth noting beyond audio: CE's semantic layer also calls OpenAI. It degrades safely — a
failed interpretation is treated as *no* evidence and the deterministic path continues — so
text conversations still work, but the Wild would run with the semantic reconcilers
effectively silent. A complete Wild deserves a funded account.

## 10. Safety ledger

| | |
|---|---|
| outbound | OFF before, during, after — never armed; 0 sends |
| tester | reset to zero state after probes; agenda intact (14 appointments) |
| conversation semantics | unchanged |
| production | untouched |
| schema | unchanged |
| deployment identity | `bb2546d` verified across image, baked SHA and stamped id |
