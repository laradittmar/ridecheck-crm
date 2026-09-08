PROJECT: RIDECHECK_CRM
TYPE: CLOSEOUT
MILESTONE: OPS-N8N-WEDGE-RECOVERY

STATUS: CONDITIONAL_PASS
DATE: 2026-09-08
SCOPE: crm_test only. No code change. Backend not restarted. Outbound OFF throughout.

---

## 1. The premise was wrong, and it was my error

**There was no wedge. n8n was healthy the entire time.**

I reported that executions 1475/1476 were stuck and that n8n created no executions. Both
claims were false, and they were false for one reason: n8n's sqlite runs in **WAL mode**,
and I copied `database.sqlite` out of the container **without its `-wal` companion file**.
I was reading a stale snapshot frozen at the last checkpoint and treating the absence of
rows as proof of absence of executions.

Reading the same database with the WAL present:

| execution | I reported | actual |
|---|---|---|
| 1475 | stuck `running` since 21:13 | **success**, stopped 2026-09-07 21:13:34 |
| 1476 | queued `new`, never started | **success**, stopped 2026-09-07 21:13:49 |
| 1478/1479/1480 | never created | created, ran, **`error`** |
| 1481/1482 | never created | created, **success** |

This is the project's own rule — *missing evidence is not false evidence* — and I broke it.
The correction matters practically: the restart the owner authorised was **not necessary**,
and had I stopped at the restart I would have "fixed" a problem that never existed while
leaving the real one untouched.

## 2. What actually stopped the replies

n8n ran all three of the owner's messages. Each failed in ~2-3 seconds at the same node:

```
executionId 1478  lastNodeExecuted "Transcribe Audio"
errorNodeType    n8n-nodes-base.httpRequest
errorMessage     "Bad gateway - the service failed to handle your request"
```

All three inbound messages were **voice notes**. The audio-transcription HTTP node received
a 502 from its upstream, the workflow aborted there, and the CE node was never reached —
which is why `thread 2042` had `lead_id` NULL and no reply was possible, entirely
independently of the outbound kill switch.

Both upstreams answer correctly now from inside the n8n container
(`api.openai.com` → 401, `graph.facebook.com` → 400 to unauthenticated probes), so the 502
was transient or request-specific. **It has not been reproduced, and it is not fixed.**

## 3. Phase 1 — pre-restart capture

| | |
|---|---|
| container | `43879bd4be63`, `n8nio/n8n:latest`, up since 2026-09-01T19:53:41Z |
| restartCount | 1 |
| workflow | `DaFqDIzVi1f92Hvz`, active=1 |
| memory / CPU | 339.8 MiB / 1 GiB (33%), CPU 0.40% — idle, not exhausted, not spinning |
| processes | all sleeping (`S`); none in `R` or `D` |
| exec 1475 / 1476 | appeared `running` / `new` **in the WAL-less snapshot only** |
| container logs at 1475 | none retained (docker log window starts 2026-09-01) |
| event log at 1475 | `n8n.workflow.success` — the decisive record |

## 4. Phase 2 — restart

Restarted `ridecheck-crm-n8n-1` only, at **2026-09-08T18:42:06Z**. Clean startup, workflow
re-activated, no errors. Two startup lines worth recording: *"Processed 1 draft workflows,
0 published workflows"* and a Python-task-runner notice (JS runner registered normally;
the workflow uses JS).

Backend was **not** restarted — still up since 15:37:33Z on image
`w4f3-bookprice-2e10469`. Postgres untouched. No compose env changed.

## 5. Phase 3 — replay

**No replay.** 1475 and 1476 were not resumed or re-run (they had already completed the day
before), no stored customer payload was replayed, and no execution was created by the
restart. **Zero** outbound sends. Nothing was hidden because nothing happened.

## 6. Phase 4 — end-to-end transport probe

First probe used a synthetic non-customer wa_id and was **correctly refused** by the
closed-beta allowlist:

```
WHATSAPP_WEBHOOK_CLOSED_BETA_NOT_ALLOWED wa_id=...0999 — no contact/thread/message/AI-event created
```

That control is working, and it means only the allowlisted tester identity can traverse this
path at all. The probe was therefore re-sent as the tester with a **text** body — text
deliberately, to bypass the transcription node and isolate transport. Phase 6 wiped it
afterwards.

Full chain proven:

```
18:43:56  backend   WHATSAPP_WEBHOOK_STORED thread_id=2042
          n8n       execution 1482 created → n8n.workflow.success
18:44:17  n8n→CE    POST /api/conversation/handle (from 172.18.0.3) → 200 OK
          CE        M18 handle thread_id=2042 action=service_gate_blocked latency_ce_ms=759
18:44:17  gate      OUTBOUND_GATE_KILL_SWITCH blocked_id=6102
```

`backend → n8n → workflow → CE` **PASS**. CE was reached through the real transport, not
called directly. The reply attempt was blocked by the kill switch exactly as required, and
the blocked row carries `path_id=CE_TEXT` / `deployment_id=2e10469` — the W4-F3 attribution
fix visible in live operation.

## 7. Phase 5 — root cause

**ROOT CAUSE: UNKNOWN (transient upstream 502 at the audio-transcription node).**

Deliberately not classified as `STUCK_EXECUTION_BLOCKED_RUNNER`, `QUEUE_WORKER_FAILURE`,
`DB_LOCK` or `RESOURCE_EXHAUSTION` — every one of those was excluded by evidence:

| candidate | excluded because |
|---|---|
| stuck execution / blocked runner | 1475 and 1476 both completed successfully |
| queue worker failure | executions ran before, during and after the incident window |
| n8n process state | idle and healthy; restart changed nothing |
| DB lock | writes were landing in the WAL the whole time |
| resource exhaustion | 33% memory, 0.4% CPU, disk 44% |

What is proven: the transcription HTTP node received a 502 three times in ~13 seconds. What
is **not** proven: why. The restart did not fix it and cannot have — the failure was
upstream of n8n, and no audio message has been retried since. **A restart masked nothing
here, but it also resolved nothing.**

## 8. Phase 6 — tester evidence and second reset

Evidence preserved before deletion — including the n8n sqlite **with** its `-wal` and
`-shm`, which is the artifact that corrected the misdiagnosis:

```
/opt/ridecheck-crm-forensics/OPS-N8N-WEDGE-RECOVERY_tester_2049_20260908T184620Z.tar.gz
sha256 cf823f335031c30198118e74bad9ba08a2337177e3c747f1b8404625531b893a
```

Contents: contact 2049, thread 2042, lead 145, 5 messages (the three voice notes, the probe
inbound, the blocked outbound), ai_events 134-137, thread state.

Then reset to approved zero state, same certified FK order:

| entity | count |
|---|---|
| contacts, threads, thread_states, candidates | 0 |
| messages, thread_revisions, revisions, leads | 0 |
| ai_events, outbound_dedup, recipient_locks | 0 |

The three pre-recovery messages cannot become the start of the next Wild. Unrelated data
intact: 14 week appointments, 14 demo leads, 32 contacts.

## 9. Phase 8 — smallest useful recurrence detection

An alert loop already exists (`unanswered_alert`, 120 s threshold) and it **could not** have
caught this. Its query requires `reply_required = true AND alert_eligible = true`, and both
flags are set **by CE**. Because CE never ran, the three events stayed:

```
id 134/135/136  status='triggered'  reply_required=NULL  alert_eligible=NULL
id 137 (probe)  status='processed'  reply_required=t     alert_eligible=t
```

Invisible to the alert, permanently. That is the gap, and it is one row-shape away from
being covered.

**RECOMMENDATION — one query, no new subsystem:** extend the existing
`unanswered_alert_loop` with a second candidate set:

```sql
SELECT id, thread_id, wa_message_id
FROM ai_events
WHERE status = 'triggered'
  AND unanswered_alert_sent_at IS NULL
  AND created_at < NOW() - INTERVAL '180 seconds'
```

`status='triggered'` means the backend forwarded to n8n and CE never came back. It needs no
schema change, no n8n redesign and no new process — it reuses the loop, the threshold
pattern and the existing `unanswered_alert_sent_at` de-duplication. It would have fired
about three minutes after 18:30:54 instead of the failure being found only because the owner
asked why there was no answer.

Suggested as a follow-up milestone, not implemented here — this milestone authorised no code
change.

## 10. Safety ledger

| | |
|---|---|
| n8n restarted | YES (authorised) |
| backend restarted | NO |
| postgres restarted | NO |
| compose env changed | NO |
| code changed | NO |
| workflow edited | NO |
| credentials touched | NO |
| n8n DB modified | NO — read-only copies only; 1475/1476 never hand-edited |
| crm_test DB written | YES, and only the Phase 6 tester reset, which this milestone requires |
| production | untouched |
| outbound | OFF before, during and after; 0 sends |
