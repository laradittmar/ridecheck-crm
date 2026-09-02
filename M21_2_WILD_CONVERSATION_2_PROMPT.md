# M21.2 — Wild Conversation 2: End-to-End WhatsApp Validation

## Objective

Validate the completed M21.1 Semantic Conversation Engine through the real transport path:

```text
WhatsApp test number
→ Meta webhook
→ n8n transport/debounce/context assembly
→ POST /api/conversation/handle
→ Conversation Engine
→ crm_test state
→ controlled outbound to the approved test recipient
```

M21.2 is an integration-validation milestone, not a new feature milestone.

## Baseline

Expected branch:
`fix/m21.1.1-primary-flow-regression`

Expected HEAD:
`dc54775`

Expected image:
`ridecheck-crm-backend:m21.1.7-dc54775`

Before any work:

```bash
cd /opt/ridecheck-crm-release-candidate
git status --short
git branch --show-current
git rev-parse HEAD
git log -12 --oneline
```

If HEAD is later than `dc54775`, audit intervening commits and work forward.

Do not reset, rebase, squash, force-checkout, or rewrite history.

## Hard safety rules

Production must remain untouched.

- Do not connect to production `crm`.
- Use `crm_test` only.
- Do not message any recipient except the explicitly approved test recipient.
- Do not rotate Meta credentials.
- Do not push.
- Do not deploy a new production release.
- Do not enable unrestricted outbound.
- Do not enable any legacy n8n AI fallback.
- Do not modify pricing values, viáticos, scheduling logic, or semantic rules just to make a live test pass.
- Do not run destructive DB cleanup outside `crm_test`.
- Redact phone numbers in reports/log excerpts.
- Never print tokens/secrets.
- If the runtime cannot be isolated from production recipients, STOP.

## Mandatory STOP gate

This prompt has two stages:

### Stage A — Readiness audit and dry-run
Safe to execute immediately.

### Stage B — Controlled live WhatsApp test
Requires explicit owner approval after Stage A.

Do NOT start Stage B merely because Stage A passes.

At the end of Stage A, STOP and return the readiness report plus the exact runtime changes required for Stage B.

# STAGE A — READINESS AUDIT

## A1 — Runtime topology audit

Trace and report:

1. Meta webhook endpoint.
2. n8n workflow receiving inbound WhatsApp.
3. lead/thread creation or lookup.
4. debounce implementation.
5. audio transcription path.
6. image-description path.
7. recent-message/context assembly.
8. exact payload sent to `/api/conversation/handle`.
9. CE response contract.
10. outbound dispatch node/service.
11. kill-switch location.
12. recipient safety gate.
13. DB connection used by backend.
14. DB connection used by n8n.
15. whether legacy n8n AI nodes are reachable.
16. whether `CONVERSATION_ENGINE_DIRECT_WEBHOOK_ENABLED` is false/absent.

Provide an ASCII topology of the actual current flow.

## A2 — Environment isolation proof

Prove the intended test runtime uses `crm_test`.

Required:

```text
backend DB target: crm_test
n8n DB/API target: test-safe backend / crm_test
production crm: not connected
```

Inspect config without printing secrets.

If any component points to production `crm`, STOP.

## A3 — Recipient safety proof

Audit existing outbound recipient safety.

Required:

```text
allowed recipient: owner-approved test number only
all other recipients: blocked
```

Report masked number only.

If no reliable allowlist exists, STOP.

## A4 — Kill-switch audit

Report implementation, current state, block point before Meta dispatch, and whether recipient allowlist + kill switch can coexist safely.

For Stage A, keep outbound blocked.

## A5 — Legacy AI fallback reachability audit

Required outcome:

```text
canonical path: n8n transport → CE
legacy AI fallback: not reachable during test
```

Do not delete legacy nodes yet.

If legacy AI can execute during the same inbound, STOP.

## A6 — Burst/context contract proof

Inspect the exact payload n8n sends to CE.

Document:

- current inbound message;
- `recent_user_messages`;
- lead/thread identifiers;
- candidate/context fields;
- transcript representation;
- image-description representation;
- source/channel fields if already present;
- timestamps/order semantics.

For a synthetic burst:

```text
Es un Focus
2019
Está en Palermo
¿Cuánto sale?
```

show the sanitized CE request shape.

Do not call Meta.

## A7 — Local endpoint smoke

Using `crm_test` only and no external outbound, run:

### DRY01 — Deferred interest
`Hola por ahora estoy buscando un auto agende esto para no perderlo asijina vez q decida aviso`

Expected: deferred response meaning; no candidate, price, Flow, or needs_human.

### DRY02 — Multi-fact quote
`Quiero revisar un Focus 2019 que está en Palermo. ¿Cuánto sale?`

Expected: vehicle/year/location understood; no redundant asks.

### DRY03 — Fuzzy confirmation
`ford ksl 2019 en Palermo`

Expected: `¿Es un Ford Ka?`; no candidate before confirmation.

### DRY04 — Origin/location role
`Yo vivo en Tigre pero el auto está en Palermo. Es un Corolla 2020.`

Expected pricing-facing location Palermo.

### DRY05 — Inspectability
`No arranca, pero está armado, completo y se puede revisar.`

Use established candidate context. Expected no repeated inspectability clarification.

### DRY06 — Formulario 12
Exact existing boundary.

### DRY07 — Motorcycle
Manual/human path, no auto quote.

### DRY08 — Multi-message burst
Use the exact context shape n8n will send.

Capture sanitized request/response evidence.

## A8 — DB-state assertions

For each dry run verify relevant `crm_test` rows:

- lead;
- thread;
- thread state;
- candidate;
- pending fuzzy key;
- needs_human;
- revision/booking side effects;
- outbound records if local simulation creates them.

Do not query production.

## A9 — Regression sanity

Run all M21 semantic suites including M21.1.7, then the correct seven-file M20 gate. No regressions accepted.

## A10 — Stage A report and STOP

Write:

`/opt/ridecheck-crm/forensics/M21_2_stageA_readiness_20260812.md`

Return:

```text
M21.2 STAGE A — PASS / PARTIAL PASS / FAIL

Branch:
<branch>

HEAD:
<SHA>

Backend image:
<tag>

Production DB connected:
YES/NO

Test DB:
<safe db name>

Recipient allowlist:
PASS/FAIL

Allowed recipient:
<masked>

Kill switch currently:
ON/OFF

Legacy n8n AI reachable:
YES/NO

Canonical n8n → CE path:
PASS/FAIL

Burst ordering/context:
PASS/FAIL

Local CE dry-runs:
<PASSED>/<FAILED>

DB-state assertions:
PASS/FAIL

M21 semantic regression:
<PASSED>/<FAILED>

M20 gate:
<PASSED>/<FAILED>

External calls made:
NO

Runtime changed:
NO

Production touched:
NO

Ready for controlled live Stage B:
YES/NO

Exact runtime changes required for Stage B:
<list>

Report:
<path>
```

Then STOP.

# STAGE B — CONTROLLED LIVE WHATSAPP VALIDATION

Run only after explicit owner approval.

## B1 — Controlled runtime activation

Apply only the runtime changes explicitly approved after Stage A.

Requirements:

- backend uses validated M21.1.7 image or approved later corrective image;
- test-safe DB remains `crm_test`;
- n8n canonical transport path active;
- legacy AI fallback unreachable;
- recipient allowlist permits only the approved test number;
- outbound remains constrained.

Record every runtime change.

## B2 — Clean test state

Before live testing, clear only the approved test recipient's pre-existing test data in `crm_test`.

Never touch production.

## B3 — Transport proof

Send one benign live WhatsApp message from the approved test phone.

Suggested:
`Hola`

Trace:

```text
Meta event
→ n8n execution
→ debounce
→ CE request
→ CE action
→ outbound reply
```

Prove one inbound, one effective CE cycle, no duplicate outbound, correct thread/lead linkage, correct `crm_test` writes.

If duplicate processing or wrong DB appears, STOP.

## B4 — Controlled scripted live cases

### LIVE01 — Deferred interest
Use the real-client deferred message.
Verify no candidate, quote, Flow, scheduling, or needs_human.

### LIVE02 — Exact commercial qualification
Known exact catalog vehicle + year + Palermo + price request.
Verify no redundant ask.

### LIVE03 — Fuzzy vehicle confirmation
Send `ford ksl 2019 en Palermo`.
Expect `¿Es un Ford Ka?`
Then `sí`.
Verify pending lifecycle and exactly one candidate.

### LIVE04 — Location role
`Yo vivo en Tigre pero el auto está en Palermo.`
Verify inspection location Palermo.

### LIVE05 — Inspectability
`No arranca, pero está armado, completo y se puede revisar.`
Verify no repeated clarification.

### LIVE06 — Formulario 12
Verify exact deterministic boundary.

### LIVE07 — Motorcycle
Verify manual RideCheck/handoff and no automotive quote.

### LIVE08 — Multi-message debounce burst
Send quickly:
`Es un Focus`
`2019`
`Está en Palermo`
`Cuánto sale?`

Verify intended debounce grouping, one CE interpretation, no duplicate outbound, no repeated asks.

### LIVE09 — Voice note
Send a real voice note equivalent to:
`Hola, estoy viendo un Peugeot 3008 2021. Yo vivo en San Isidro pero el auto está en Villa Urquiza. No arranca porque está sin batería, pero está completo y se puede revisar. Quería saber cuánto cuesta.`

Verify transcript reaches CE and semantics are correct.

### LIVE10 — Correction
`Es un Ford Ka 2020... no, perdón, es un Ford Kuga 2020 y está en Palermo.`
Verify correction wins.

## B5 — Wild phone session

After scripted cases pass, conduct one unscripted owner-driven conversation with typo, voice note, fast messages, FAQ, correction, location change, ambiguity, and irrelevant info.

Capture sanitized trace afterward.

## B6 — Evidence per case

Capture:

```text
case ID
sanitized inbound
n8n execution ID
debounced burst
sanitized CE request
CE action
sanitized outbound
lead/thread/candidate IDs
state changes
needs_human
pricing/scheduling side effects
duplicate count
PASS/FAIL
```

## B7 — Defect classification

Classify defects by layer:

- Meta transport;
- n8n inbound;
- debounce;
- context assembly;
- Whisper;
- CE semantic logic;
- field-evidence resolver;
- pricing;
- outbound;
- DB/state;
- test config.

Severity: BLOCKER / HIGH / MEDIUM / LOW.

## B8 — Fix discipline

Only BLOCKER/HIGH defects required for acceptance should be fixed here.

For any fix:

- stop live outbound first;
- re-enable kill switch;
- reproduce locally;
- add regression test;
- make narrow change;
- rerun owning suite + M21.1.7 + M20;
- build new image if backend changed;
- request owner approval before reactivating live test runtime.

No silent hotfixes.

## B9 — Acceptance

M21.2 can PASS only if:

1. canonical WhatsApp → n8n → CE path proven;
2. `crm_test` isolation proven;
3. recipient safety proven;
4. no duplicate processing;
5. deferred case passes;
6. fuzzy lifecycle passes;
7. location role passes;
8. inspectability passes;
9. deterministic boundaries pass;
10. real debounce burst passes;
11. voice note reaches CE correctly;
12. no unresolved BLOCKER/HIGH defect remains.

## B10 — Closeout

Write:

`/opt/ridecheck-crm/forensics/M21_2_wild_conversation_2_closeout_20260812.md`

Return:

```text
M21.2 — PASS / PARTIAL PASS / FAIL

Validated commit:
<SHA>

Validated image:
<tag>

Test DB isolation:
PASS/FAIL

Recipient safety:
PASS/FAIL

Canonical n8n → CE transport:
PASS/FAIL

Legacy n8n AI inactive:
PASS/FAIL

Single processing per burst:
PASS/FAIL

Duplicate outbound:
<number>

LIVE01 deferred:
PASS/FAIL

LIVE02 exact qualification:
PASS/FAIL

LIVE03 fuzzy lifecycle:
PASS/FAIL

LIVE04 location roles:
PASS/FAIL

LIVE05 inspectability:
PASS/FAIL

LIVE06 F12:
PASS/FAIL

LIVE07 motorcycle:
PASS/FAIL

LIVE08 debounce burst:
PASS/FAIL

LIVE09 voice:
PASS/FAIL

LIVE10 correction:
PASS/FAIL

Wild owner session:
PASS/FAIL

BLOCKER defects:
<number>

HIGH defects:
<number>

MEDIUM defects:
<number>

LOW defects:
<number>

Unresolved launch-blocking defect:
YES/NO

Production touched:
NO

M21.2 complete:
YES/NO

Safe for Architecture Closeout AC-2/AC-3 and M21.3:
YES/NO

Report:
<path>
```
