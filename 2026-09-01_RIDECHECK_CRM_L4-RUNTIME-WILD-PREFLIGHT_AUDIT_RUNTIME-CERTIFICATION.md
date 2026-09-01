PROJECT: RIDECHECK_CRM
TYPE: AUDIT
MILESTONE: L4-RUNTIME-WILD-PREFLIGHT
STATUS: CONDITIONAL NO-GO — IMAGE REMEDIATION REQUIRED
DATE: 2026-09-01
COMMIT: 8b182d5a7ca58e5311091040e49fd89408c374d7
OUTBOUND: OFF (OUTBOUND_ENABLED=false)
PRODUCTION DB TOUCHED: NO
WILD SESSION ATTEMPTED: NO
BLOCKER_COUNT: 1
PRE-EXISTING_RISK_COUNT: 3 (all classified, none new)

---

# L4 — Runtime Wild Preflight Audit

## Executive Summary

**CONDITIONAL NO-GO.** One blocking finding: the running backend image is
`l1-semantic-820f4d6`, not the certified `l2.1-email-3131f88`. The beta
compose overlay was not applied at startup, so L2 path_id fixes and L2.1
alert improvements are absent from the live container. The correct image exists
locally and can be deployed with a single compose command.

All other subsystems are at or above Wild baseline: WA token live (HTTP 200),
n8n active with last successful execution, outbound OFF, crm_test isolated,
tester-only allowlist enforced, booking flow private key present, 104/104
frozen gate smokes PASS.

**Remediation required before Wild:**
1. Deploy `l2.1-email-3131f88` via beta compose overlay (command in Part 18)
2. Verify 4-file hash parity
3. Re-run 104-test gate smoke
4. Owner sets `BETA_OUTBOUND_ENABLED=true`
5. Wild session authorized

---

## Safety Constraints (All Respected)

- OUTBOUND OFF throughout preflight
- Production DB not touched (crm_test only)
- No WhatsApp messages sent during preflight
- No Meta configuration changes
- No n8n business logic changes
- No CE semantics changed
- No secrets printed; no full tokens exposed
- No L1/L2/L3 patches (no contradictory evidence found)

---

## Part 1 — Runtime Identity

| Item | Value |
|------|-------|
| GIT HEAD | `8b182d5a7ca58e5311091040e49fd89408c374d7` |
| RUNNING IMAGE | `ridecheck-crm-backend:l1-semantic-820f4d6` |
| REQUIRED IMAGE | `ridecheck-crm-backend:l2.1-email-3131f88` |
| DATABASE | `postgresql+psycopg://crm:***@postgres:5432/crm_test` |
| ALEMBIC HEAD | `20260831_wild01_dedup_causal_inbound` |
| OUTBOUND_ENABLED | `false` |
| CONVERSATION_ENGINE_DIRECT_WEBHOOK_ENABLED | `NOT_SET` (effectively false) |
| CLOSED_BETA_ALLOWED_WA_IDS | `5491153368330` |
| QUARANTINED_TEST_WA_IDS | `""` (empty) |

**SOURCE/RUNTIME PARITY: FAIL**

The beta compose file pins `image: ridecheck-crm-backend:l2.1-email-3131f88` but the
backend was started without the beta compose overlay. The correct image exists
locally but is not running.

---

## Part 2 — File-Level Parity (Running vs. RC Source)

| File | Running Image | RC Source (l2.1) | Match |
|------|--------------|-------------------|-------|
| conversation_engine.py | sha=00ace84…  size=295376 | sha=00ace84…  size=295376 | ✓ MATCH |
| outbound_safety_gate.py | sha=2c9588e…  size=24723 | sha=2c9588e…  size=24723 | ✓ MATCH |
| whatsapp.py | sha=09ad046…  size=31102 | sha=09ad046…  size=31102 | ✓ MATCH |
| booking_flow_service.py | sha=d6f0ae7…  size=28653 | sha=d6f0ae7…  size=28653 | ✓ MATCH |
| buscando_followup.py | sha=9be3c0c…  size=4315 | sha=3b591a4…  size=4475 | ✗ MISMATCH |
| quote_followup.py | sha=37aef83…  size=4413 | sha=72f9b02…  size=4573 | ✗ MISMATCH |
| unanswered_alert.py | sha=df9b2e4…  size=9158 | sha=94eb88c…  size=9348 | ✗ MISMATCH |
| resend_email.py | sha=9ac79e1…  size=18792 | sha=4921401…  size=21321 | ✗ MISMATCH |

**Impact of mismatches:**
- `buscando_followup.py` / `quote_followup.py`: L2-transport path_id fix absent
  → followup sends proceed without `path_id`, triggering OUTBOUND_ATTEMPT_WITH_UNKNOWN_SOURCE
- `unanswered_alert.py` / `resend_email.py`: L2.1 additions absent
  → scheduling handoff alert and unanswered-thread alert functions not present

**CE itself (conversation_engine.py) is CORRECT — same sha as certified source.**
**Safety gate (outbound_safety_gate.py) is CORRECT — same sha as certified source.**

---

## Part 3 — Frozen Gate Smokes

All four certified gates pass. No contradictory evidence introduced.

```
L1 (test_l1_semantic_authority.py):       19/19 PASS
L2 (test_l2_transport_path_integrity.py): 20/20 PASS
L2.1 (test_l2_1_email_alerts.py):         15/15 PASS
L3 (test_l3_dirty_history.py):            50/50 PASS
─────────────────────────────────────────────────
Total gate smokes:                        104/104 PASS
```

Gate smokes test against RC source, not the running container image. The 4-file
mismatch would cause L2 transport gate failures in the running image — this is
precisely what the image upgrade remediates.

---

## Part 4 — n8n Transport Tier

| Item | Status |
|------|--------|
| n8n service | RUNNING (Up 10 days, healthy) |
| Workflow | "CRM - Ridecheck" id=`DaFqDIzVi1f92Hvz` |
| Workflow active | YES (`active=1`) |
| Last execution | 2026-08-31 20:15:23 — status=success |
| Webhook host | `n8n.ridecheck.ar` |
| WEBHOOK_URL | `https://n8n.ridecheck.ar` |

**n8n STATUS: OPERATIONAL**

The canonical conversation path `WhatsApp webhook → n8n → POST /api/conversation/handle`
is active. The n8n workflow correctly calls CE on every inbound message. CE
returns `handled=true`, preventing the legacy AI fallback branch from firing.

---

## Part 5 — Direct Path / CE Bypass Risk

| Item | Value |
|------|-------|
| CONVERSATION_ENGINE_DIRECT_WEBHOOK_ENABLED | `NOT_SET` (→ false) |
| Direct webhook path | DISABLED |
| n8n required services provided | audio transcription, 20s debounce, context aggregation, lead find/create |
| LEGACY_N8N_AI_PIPELINE | IN CODE as LEGACY_PATHS — blocked by safety gate |

**DIRECT BYPASS RISK: NONE.** CONVERSATION_ENGINE_DIRECT_WEBHOOK_ENABLED not
set, n8n provides all required transport services, and the legacy path is
classified as retired in `outbound_path_registry.py`.

---

## Part 6 — Control Dashboard

| Route | HTTP Code | Assessment |
|-------|-----------|------------|
| `GET /control` | 303 → /login | Auth required — correct behavior |
| `GET /api/ops/summary` | 200 | Publicly accessible (stats only) |
| `GET /api/ops/threads` | 200 | Accessible |
| `GET /agenda` | 404 | Not in running image (l1-semantic) |
| `GET /api/agenda/blocks` | 404 | Not in running image |

**Ops summary at time of preflight:**
```json
{
  "outbound_enabled": false,
  "inbound_count": 0,
  "outbound_count": 0,
  "blocked_count": 0,
  "waiting_customer_count": 1,
  "critical_events_count": 332,
  "processing_failures_count": 0
}
```

Note: `critical_events_count=332` reflects a time-windowed count from the ops
API; the DB total is 733. All 733 events are `OUTBOUND_ATTEMPT_WITH_UNKNOWN_SOURCE`
BLOCKER events with `detected_at=NULL` — they predate the security event
timestamp column and are all attributable to the l1-semantic image lacking the
L2 path_id fix. See Part 7.

---

## Part 7 — Security Endpoint Baseline

| Metric | Value at Preflight |
|--------|--------------------|
| Outbound ledger total | 38 messages |
| Last outbound message ID | 5695 |
| Last outbound timestamp | 2026-08-31 20:15:46 |
| Last inbound message ID | 5694 |
| Last inbound timestamp | 2026-08-31 20:15:23 |
| Outbound dedup records | 23 |
| Unauthorized path events (API) | 0 |
| Security events (DB total) | 733 — all OUTBOUND_ATTEMPT_WITH_UNKNOWN_SOURCE, detected_at=NULL |
| Outbound to non-tester wa_ids | 0 — confirmed |

**Security event classification:**
All 733 security events are `OUTBOUND_ATTEMPT_WITH_UNKNOWN_SOURCE` with
`detected_at=NULL`. These events were generated when the l1-semantic image
(missing L2 path_id fix) attempted to send followup messages without passing
`path_id` to `gate.attempt()`. Since OUTBOUND was OFF during all these
attempts, no WhatsApp message was actually sent. The events are pre-existing
and not new unauthorized paths. After image upgrade to l2.1-email, all CE
outbound calls will carry a valid `path_id`.

**OUTBOUND FORENSIC SAFETY: CONFIRMED** — 0 messages sent to any non-tester
wa_id. All 38 outbound messages target the tester (wa_id=...8330).

---

## Part 8 — Booking Flow Runtime Health

| Item | Status |
|------|--------|
| FLOW_BOOKING_PRIVATE_KEY_PATH | `/run/secrets/flow_booking_private.pem` |
| Private key present | YES (1704 bytes — RSA 2048 PEM) |
| WHATSAPP_BOOKING_FLOW_ID | NOT SET in env → default `28104222025943520` |
| Flow endpoint registered | YES: `POST /integrations/whatsapp/flows/booking/data-exchange` |
| Flow endpoint also at | `POST /api/whatsapp/thread/{thread_id}/send-flow` |
| Flow state in Meta | DRAFT (not published — by design, awaiting owner publish) |
| booking_flow_service.py sha | MATCH (d6f0ae7…) |

**BOOKING FLOW RUNTIME: READY** (key present, endpoint registered, service
correct). Meta publish remains pending owner action per M21.3-C-D design.

---

## Part 9 — Email / Resend Runtime Status

| Item | Running Image (l1-semantic) | RC (l2.1-email) |
|------|----------------------------|-----------------|
| RESEND_API_KEY | PRESENT (prefix re_7nvrG…) | same |
| Email implementation | Resend HTTPS API | Resend HTTPS API |
| SMTP_PASSWORD | EMPTY | EMPTY |
| Booking confirmation | FUNCTIONAL | FUNCTIONAL |
| Scheduling handoff alert | ABSENT (L2.1 add) | PRESENT |
| Unanswered-thread alert | ABSENT (L2.1 add) | PRESENT |

**Assessment:** Core booking confirmation email is functional in the running
image (uses Resend API, key present). Missing L2.1 additions mean scheduling
handoff alerts and unanswered-thread alerts will not fire. These are operational
quality features, not safety blockers. Will be restored after image upgrade.

---

## Part 10 — WhatsApp Token Status

| Item | Value |
|------|-------|
| WHATSAPP_TOKEN | PRESENT (len=207, non-JWT / system user EAT) |
| Meta API live ping | HTTP 200 ← TOKEN VALID |
| WHATSAPP_PHONE_NUMBER_ID | PRESENT |
| Token rotated | N/A — no rotation performed (not authorized) |

**WA TOKEN: VALID.** Live API ping confirms the token is accepted by Meta.
Token type is a non-JWT extended access token (standard for system users).

---

## Part 11 — App Secret Risk Classification

| Item | Value |
|------|-------|
| WHATSAPP_APP_SECRET | IN ENV but len=0 (empty string) |
| Signature verification | SKIPPED (dev mode — see `_verify_signature` in whatsapp.py:56-58) |
| Risk classification | RISK-03 (pre-existing from M21.6-SYSTEM-AUDIT) |

**Code behavior (whatsapp.py:55-72):**
```python
def _verify_signature(raw_body, signature_header, app_secret):
    secret = (app_secret or "").strip()
    if not secret:
        logger.info("Webhook signature skipped (dev mode)")
        return True  # accepts any POST
```

**Risk assessment:** With App Secret empty, any HTTP POST to the WhatsApp
webhook is accepted without signature verification. This allows:
- Replay attacks against the CE
- Spoofed webhook payloads

**Compensating controls for Wild:**
1. `CLOSED_BETA_ALLOWED_WA_IDS=5491153368330` — CE ignores messages from any
   other wa_id; an attacker would need to know and spoof the exact tester wa_id
2. `OUTBOUND_ENABLED=false` — even if CE processes a spoofed payload, no
   outbound message is dispatched (or under live: the safety gate blocks unknown paths)
3. crm_test isolation — any spurious state mutation is contained to test DB

**Classification: ACCEPTED RISK for controlled tester-only Wild.** This is not
new; it was documented in M21.6-SYSTEM-AUDIT. App Secret must be set before
any public launch.

---

## Part 12 — Compensating Controls for Tester-Only Wild

| Control | Status | Coverage |
|---------|--------|----------|
| OUTBOUND_ENABLED=false | ACTIVE | Blocks all automated sends until owner enables |
| CLOSED_BETA_ALLOWED_WA_IDS=5491153368330 | ACTIVE | CE routes only tester messages |
| QUARANTINED_TEST_WA_IDS="" | ACTIVE | No wa_ids in quarantine |
| crm_test isolation | ACTIVE | Production DB unreachable from this container |
| OutboundSafetyGate | ACTIVE | All automated outbound must pass gate |
| path_id enforcement | PARTIAL | CE paths have path_id; followup services don't (l1 image bug) |
| Allowlist enforcement | ACTIVE | 33 contacts in DB, 0 outbound to non-tester |
| n8n webhook auth | ACTIVE | n8n validates Meta webhook signature |

**Summary:** Safety posture is adequate for a single-tester Wild session.
The path_id gap (followup services in l1 image) will fire BLOCKER security
events but will not cause unauthorized outbound under current OUTBOUND=false.
After image upgrade, path_id gap is closed.

---

## Part 13 — Tester Identity and Current DB State

| Item | Value |
|------|-------|
| Tester wa_id | 5491153368330 |
| Contact record | id=2, name="Lara Dittmar" |
| Thread record | id=2 |
| In CLOSED_BETA_ALLOWED_WA_IDS | YES |
| Contact in DB | YES |

---

## Part 14 — Tester Lifecycle State

| Field | Value |
|-------|-------|
| `last_stage` | `QUOTED` |
| `current_focus_candidate_id` | 129 (SUV_4X4_DEPORTIVO 2015, Sur/Berazategui) |
| `cycle_reset_pending` | `False` |
| `customer_name` | Lara |
| `home_zone_group` | Sur |
| `current_cycle_started_at` | 2026-08-27 19:20:56 UTC |
| Last outbound message | id=5695, 2026-08-31 20:15:46 — "¡Hola! ¿En qué puedo ayudarte hoy?" |
| Last inbound message | id=5694, 2026-08-31 20:15:23 — "hola" |

**State assessment:** Tester is MID-CYCLE in QUOTED stage. The most recent
exchange (2026-08-31) was a greeting ("hola" → bot greeting response), which
did not advance or reset the stage. The last meaningful state is QUOTED with
an active SUV_4X4_DEPORTIVO 2015 candidate in Berazategui.

**For Wild session:** The tester can either (a) continue from QUOTED by
sending an acceptance or follow-up question, or (b) trigger a cycle reset by
the owner via DB or by sending a reset-triggering message. No DB intervention
is required for Wild to proceed; CE will handle whatever state the tester
arrives in. Owner should be briefed on current stage before Wild.

---

## Part 15 — Demo Data Safety

| Metric | Value |
|--------|-------|
| Total contacts in crm_test | 33 (1 tester + 32 pre-existing) |
| Total threads | 33 |
| Total candidates | 6 |
| Total outbound messages | 38 |
| Outbound to non-tester wa_ids | 0 |
| Thread revisions | 2 |
| DB isolated from production | YES (crm_test != crm) |

**DEMO DATA SAFETY: PASS.** No outbound message has ever been sent to any
non-tester wa_id in crm_test. The 32 non-tester contacts are pre-existing
seeded data (leads/demos) with no associated outbound activity.

---

## Part 16 — Agenda Route

| Route | HTTP | Note |
|-------|------|------|
| `GET /agenda` | 404 | Not registered in l1-semantic image |
| `GET /api/agenda/blocks` | 404 | Not registered in l1-semantic image |
| `GET /control` | 303 | Auth-gated, present |

Agenda was introduced in M21.3-UX2. It is present in the RC source and will
be reachable after image upgrade to l2.1-email. Not a preflight blocker.

---

## Part 17 — Pre-Wild Security Baseline

These values are the pre-Wild baseline. Any deviation after Wild begins
constitutes forensic evidence.

```
LAST_OUTBOUND_ID:       5695  (2026-08-31 20:15:46)
LAST_INBOUND_ID:        5694  (2026-08-31 20:15:23)
OUTBOUND_DEDUP_RECORDS: 23
SECURITY_EVENTS_TOTAL:  733   (all pre-existing, all detected_at=NULL)
OUTBOUND_NON_TESTER:    0
THREAD_REVISIONS:       2
```

After image upgrade, re-baseline these counts before enabling outbound.

---

## Part 18 — Outbound Enablement Plan

### Step 1: Deploy certified image (REQUIRED BEFORE STEP 2)

```bash
cd /opt/ridecheck-crm
docker compose \
  -f docker-compose.yml \
  -f /opt/ridecheck-crm-release-candidate/docker-compose.beta.yml \
  up -d --force-recreate backend
```

### Step 2: Verify image and file parity

```bash
docker inspect ridecheck-crm-backend-1 --format='{{.Config.Image}}'
# Expected: ridecheck-crm-backend:l2.1-email-3131f88

docker exec ridecheck-crm-backend-1 sha256sum \
  app/services/buscando_followup.py \
  app/services/quote_followup.py \
  app/services/unanswered_alert.py \
  app/services/resend_email.py
# All hashes must match RC source
```

### Step 3: Re-run gate smokes (from RC host, not container)

```bash
SNAP=4288
SITE="/var/lib/containerd/io.containerd.snapshotter.v1.overlayfs/snapshots/$SNAP/fs/usr/local/lib/python3.12/site-packages"
DATABASE_URL="sqlite:///:memory:" PYTHONPATH="$SITE:/opt/ridecheck-crm-release-candidate/backend" \
  pytest tests/test_l1_semantic_authority.py \
         tests/test_l2_transport_path_integrity.py \
         tests/test_l2_1_email_alerts.py \
         tests/test_l3_dirty_history.py -v
# Expected: 104/104 PASS
```

### Step 4: Owner enables outbound (REQUIRES EXPLICIT OWNER AUTHORIZATION)

```bash
cd /opt/ridecheck-crm
BETA_OUTBOUND_ENABLED=true \
docker compose \
  -f docker-compose.yml \
  -f /opt/ridecheck-crm-release-candidate/docker-compose.beta.yml \
  up -d --force-recreate backend
```

### Step 5: Confirm outbound state

```bash
curl http://localhost:8000/api/ops/summary | python3 -c "
import json,sys; d=json.load(sys.stdin)
print('outbound_enabled:', d['outbound_enabled'])
"
# Expected: outbound_enabled: True
```

### Step 6: Wild session authorized — owner sends first message

### Step 7: Shutdown (end of Wild session)

```bash
cd /opt/ridecheck-crm
docker compose \
  -f docker-compose.yml \
  -f /opt/ridecheck-crm-release-candidate/docker-compose.beta.yml \
  up -d --force-recreate backend
docker compose stop n8n
# Outbound reverts to false (BETA_OUTBOUND_ENABLED not set)
```

---

## Part 19 — GO/NO-GO Determination

### Mandatory GO criteria

| Criterion | Status |
|-----------|--------|
| L1 smoke PASS (19/19) | ✓ PASS |
| L2 smoke PASS (20/20) | ✓ PASS |
| L3 smoke PASS (50/50) | ✓ PASS |
| Source/runtime parity PASS | ✗ FAIL — l1-semantic running, l2.1-email required |

**READY FOR OWNER MESSAGE = NO**

### Primary blocker

**BLOCKER-L4-01: IMAGE MISMATCH**
- Required: `ridecheck-crm-backend:l2.1-email-3131f88`
- Running: `ridecheck-crm-backend:l1-semantic-820f4d6`
- Cause: backend started without beta compose overlay
- Impact: L2 path_id fix absent from followup services (buscando, quote);
  L2.1 alert functions absent (scheduling handoff, unanswered); every
  followup attempt fires OUTBOUND_ATTEMPT_WITH_UNKNOWN_SOURCE
- Remediation: single compose command (Part 18 Step 1) — image exists locally

### Pre-existing classified risks (not new, not blockers for tester-only Wild)

| Risk | Classification | Mitigation |
|------|----------------|------------|
| RISK-03: App Secret empty | ACCEPTED — tester-only Wild | allowlist + outbound gate |
| RISK-04: 733 BLOCKER security events | PRE-EXISTING — l1-semantic; resolved by image upgrade | none needed pre-upgrade |
| RISK-05: Tester MID-CYCLE (QUOTED) | ACCEPTABLE — CE handles any start state | owner briefed |

### Supplementary findings (all satisfactory)

- WA token: VALID (HTTP 200 live ping)
- n8n: ACTIVE, last execution success 2026-08-31
- Database: crm_test isolated, alembic fully migrated
- Booking flow private key: PRESENT (1704 bytes, RSA 2048)
- OUTBOUND: OFF (OUTBOUND_ENABLED=false confirmed)
- Outbound to non-tester: 0 (confirmed forensically)
- CONVERSATION_ENGINE_DIRECT_WEBHOOK_ENABLED: NOT_SET (effectively false/safe)

### Decision

```
CURRENT STATUS:  CONDITIONAL NO-GO
BLOCKER:         BLOCKER-L4-01 (image mismatch)
REMEDIATION:     Part 18 Steps 1-3 (operator)
POST-REMEDIATION: Re-evaluate GO/NO-GO — all other criteria are PASS
```

---

## Part 20 — Roadmap Update

**L4-RUNTIME-WILD-PREFLIGHT: CONDITIONAL NO-GO**

Gate remains open pending BLOCKER-L4-01 remediation. After image upgrade and
re-verification (Steps 1-3 of Part 18), L4 becomes GO and Wild session can
proceed with owner outbound authorization (Step 4).

No L1/L2/L3 gates reopened. No production code changes made in this preflight.

---

## Appendix A — Image Tag Registry (as of 2026-09-01)

| Tag | Built | Status |
|-----|-------|--------|
| `l1-semantic-820f4d6` | 2026-08-31 | CURRENTLY RUNNING — stale |
| `l2-transport-53b04e5` | 2026-09-01 | EXISTS locally, not running |
| `l2.1-email-3131f88` | 2026-09-01 | EXISTS locally — REQUIRED, not running |

The correct image for Wild is `l2.1-email-3131f88`. It supersedes all prior
tags. The beta compose file already pins to this image.

---

## Appendix B — What Was NOT Changed

- Conversation Engine behavior
- Pricing or scheduling rules
- Booking Flow (Meta publish not performed)
- OutboundSafetyGate logic
- n8n workflow (INACTIVE state not changed; workflow was already ACTIVE)
- WhatsApp token (not rotated)
- App Secret (not set — owner action required for full launch)
- Production DB (crm_test only)
- OUTBOUND_ENABLED (remains false)
- Any frozen gate (L1, L2, L2.1, L3)
- Any environment variable or compose configuration
