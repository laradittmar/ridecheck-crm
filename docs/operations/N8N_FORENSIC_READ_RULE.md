# Reading n8n execution state for forensics

**Invariant: never read n8n's SQLite without its companion files.**

`/home/node/.n8n/database.sqlite` runs in **WAL journal mode**. Copying that file alone gives
a snapshot frozen at the last checkpoint, which can be hours or days stale. Recent executions
are missing entirely, and executions that finished long ago still look `running` or `new`.

## What this cost us

On 2026-09-08, three owner voice notes appeared lost. A WAL-less copy of the database showed
executions `1475 running` and `1476 new` since the previous evening, and nothing newer. That
read produced a confident, wrong diagnosis — "n8n is wedged, it creates no executions" — and
an unnecessary container restart was authorised on the strength of it.

With the WAL included, the same database said something completely different: 1475 and 1476
had both **succeeded**, and the three messages had run as executions 1478/1479/1480 and
**failed at the `Transcribe Audio` node**. The real cause — an OpenAI balance with no credits
— was in the execution data the whole time.

Absence of rows in a stale snapshot is not absence of executions. Missing evidence is not
false evidence.

## The rule

Copy all three files together, into one directory, before opening the database:

```
database.sqlite
database.sqlite-wal
database.sqlite-shm
```

Use `scripts/n8n_forensic_copy.sh`, which does exactly this and warns if a companion file is
absent. Any SQLite-consistent method (`sqlite3 .backup`, `VACUUM INTO`) is equally acceptable.

## Prefer the event log for "what happened"

`/home/node/.n8n/n8nEventLog*.log` is append-only JSON and always current — it needs no WAL
reasoning at all. It carries, per execution:

- `n8n.workflow.started` / `n8n.workflow.success` / `n8n.workflow.failed`
- `executionId`, `lastNodeExecuted`, `errorNodeType`, `errorMessage`
- per-node `n8n.node.started` / `n8n.node.finished`

That is what finally identified the failing node. Read the event log first, then confirm
against the database with its WAL — and if the two disagree, the database copy is the thing
to distrust.

## Node-level error bodies

Full upstream error payloads live in `execution_data.data` for the execution id. That is
where `"code": "credit_balance_exhausted"` was recovered from. It is only visible with the
WAL present.
