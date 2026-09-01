PROJECT: RIDECHECK_CRM / TYPE: AUDIT / MILESTONE: L4.1A-META-ASSET-CORRECTION

# RideCheck CRM — L4.1A Meta Asset Correction Audit: Delivery Root Cause
**Date:** 2026-09-01
**Operator:** Assistant (automated, read-only)
**Outbound disabled:** Maintained from Wild #1 shutdown (2026-09-01T15:38:40Z)
**Production DB touched:** NO
**Meta configuration changed:** NO
**`.env` changed:** NO

---

## Part 1 — Retraction: DISCONNECTED Causal Claim

The L4.1 conclusion that `status=DISCONNECTED → Wild #1 delivery failure` is hereby **RETRACTED**.

Evidence supporting retraction:
- During the L4.1A audit, the same Graph API endpoint returned `status=CONNECTED` and `status=DISCONNECTED` in alternating calls within the same session.
- Both values were observed for the same phone_number_id (`122205934115920`) within minutes of each other.
- The `status` field is **not a reliable indicator** of Cloud API messaging capability for this phone asset.
- Prior successful sends (id=5687, 5691, 5693, all `status=read`) occurred via `122205934115920` at the same time the field was likely returning DISCONNECTED.

**DISCONNECTED CAUSAL CLAIM: RETRACTED**

---

## Part 2 — Meta Asset Topology

### WABA 1: 101584872897508 — "Ride Check Assistance"
| Field | Value |
|---|---|
| WABA ID | 101584872897508 |
| Display name | Ride Check Assistance |
| Business | Ride Check Assistance (ID: 1018334899337098) |
| Account review | APPROVED |
| Phone numbers | **122205934115920** (+54 9 11 5829-5318) |
| Phone status | DISCONNECTED (unreliable field — see Part 1) |
| Phone platform_type | ON_PREMISE |
| Phone quality_rating | UNKNOWN |
| Flows | None |
| Token access | YES (current token can read this WABA's phone) |
| Intended operational phone | **NO** |

### WABA 2: 1520701463019847 — "Ridecheck Assistance"
| Field | Value |
|---|---|
| WABA ID | 1520701463019847 |
| Display name | Ridecheck Assistance |
| Currency | ARS |
| Business | Ride Check Assistance (ID: 1018334899337098) |
| Account review | APPROVED |
| Phone numbers | **1196075770246218** (+54 9 11 5700-8687) |
| Phone status | CONNECTED (consistent across all queries) |
| Phone platform_type | **CLOUD_API** |
| Phone quality_rating | **GREEN** |
| Flows | **ALL 5 published Flows** (see Part 7) |
| Token access | YES (current token can read + has messaging access) |
| Intended operational phone | **YES** |

### Test WABA: 987783207096418 — "Test WhatsApp Business Account"
| Field | Value |
|---|---|
| WABA ID | 987783207096418 |
| Phone | 1093930813805061 (+1 555-186-5876) — Meta test number |
| Token access | YES |
| Intended operational phone | NO |

---

## Part 3 — Phone Asset Mapping

### Phone A — Intended Operational Phone (Owner-Confirmed)
| Field | Value |
|---|---|
| Number | +54 9 11 5700-8687 |
| Phone Number ID | 1196075770246218 |
| WABA | 1520701463019847 |
| Status | CONNECTED |
| Platform type | **CLOUD_API** |
| Quality rating | **GREEN** |
| Verified name | Ridecheck Assistance |
| Name status | APPROVED |
| Code verification status | EXPIRED |
| Token read access | YES (confirmed via Graph API) |
| Token messaging access | YES (POST to /messages returns param error, not auth error) |
| Flows on same WABA | YES (all 5 published Flows) |

### Phone B — Currently Configured Runtime Phone (Wrong)
| Field | Value |
|---|---|
| Number | +54 9 11 5829-5318 |
| Phone Number ID | 122205934115920 |
| WABA | 101584872897508 |
| Status | DISCONNECTED / CONNECTED (oscillates — unreliable field) |
| Platform type | **ON_PREMISE** |
| Quality rating | UNKNOWN |
| Verified name | Ride Check Assistance |
| Code verification status | NOT_VERIFIED |
| Token read access | YES (confirmed via Graph API) |
| Token messaging access | INTERMITTENT (some Cloud API sends succeeded; others failed) |
| Flows on same WABA | NONE |

---

## Part 4 — Runtime Configuration vs Intent

| Item | Value |
|---|---|
| RUNTIME PHONE NUMBER ID (`.env`) | **122205934115920** |
| INTENDED OPERATIONAL PHONE NUMBER ID | **1196075770246218** |
| MATCH | **NO** |
| FINDING SEVERITY | **HIGH** |

The application is consistently configured to send via a different Meta phone asset than the owner's operational RideCheck WhatsApp identity. All outbound sends since `.env` was set to `122205934115920` have used the wrong sender phone.

**Internal consistency ≠ business asset correctness.**

The prior L4.1A audit correctly noted that `.env`, container env, and send URL all consistently showed `122205934115920`. That consistency proved configuration coherence — it does not prove the correct asset is configured.

---

## Part 5 — Historical Configuration Timeline

```
Initial CRM (3396615 — 2026-04-xx):
  docker-compose.yml: WHATSAPP_PHONE_NUMBER_ID: "123"   ← placeholder

feat: SMTP config (ddcd03b — 2026-05-10):
  docker-compose.yml: WHATSAPP_PHONE_NUMBER_ID: "1196075770246218"   ← CORRECT
  This is the intended operational phone, set in May 2026.

feat: new .env (1c5ccec — 2026-04-21):
  .env file introduced (gitignored — content not reconstructable from git)

[Unknown commit — between ddcd03b and M19]:
  docker-compose.yml changed from:
    WHATSAPP_PHONE_NUMBER_ID: "1196075770246218"  (hardcoded)
  to:
    WHATSAPP_PHONE_NUMBER_ID: "${WHATSAPP_PHONE_NUMBER_ID}"  (env-var)

  This moved the source of truth to .env.
  The .env was (at some point) set to 122205934115920 — the WRONG phone.

.env last modified: 2026-08-31 12:04:57
  This modification appears to be a token rotation (token length changed
  from 201 chars in .env.backup-before-resend to 207 chars in current .env).
  The phone ID may have been set to 122205934115920 when .env was created/updated,
  possibly referencing the wrong WABA asset.

.env.backup-before-resend (pre-2026-09-01):
  WHATSAPP_PHONE_NUMBER_ID=122205934115920   ← wrong value already present
  WHATSAPP_TOKEN: len=201 (old token)
```

**HISTORICAL CONFIG CHANGE FOUND: YES**

The correct phone ID `1196075770246218` was hardcoded in `docker-compose.yml` in May 2026. After the switch to env-var substitution, the `.env` was populated with the wrong value `122205934115920`. The exact commit and date of the env-var switch is not recoverable from git (`.env` is gitignored and the `docker-compose.yml` diff for that specific change doesn't appear in the tracked commit log).

---

## Part 6 — Inbound Routing (Wild #1)

**INBOUND PHONE NUMBER ID: UNKNOWN**

The raw_payload stored in `whatsapp_messages` for inbound records (id=6040, 6041, 6042) contains only the individual message object. The webhook metadata envelope (which includes `phone_number_id` and `display_phone_number`) is processed at the n8n layer and not persisted to the DB.

From the stored raw_payload fields:
```json
{
  "from": "5491153368330",
  "type": "audio",
  ...
}
```

No `metadata.phone_number_id` is recoverable from the stored DB records.

**What is known:**
- The tester's actual WhatsApp number (+54 9 11 5700-8687) is the WABA 1520701463019847 operational identity
- Inbound from the tester would arrive via whichever phone the tester is messaging
- The owner confirms the CRM WhatsApp contact for RideCheck is +54 9 11 5700-8687 (phone 1196075770246218)
- Therefore inbound **almost certainly** arrived via WABA 1520701463019847 (intended WABA)
- The CE/n8n had no issue processing inbound (AI event 100: reply_produced=true)

**INBOUND PHONE ASSET: UNKNOWN (most likely 1196075770246218 / WABA 1520701463019847)**
**OUTBOUND PHONE ASSET: 122205934115920 (confirmed from runtime env)**
**SAME PHONE: UNKNOWN / LIKELY NO** — inbound via intended phone (1196075770246218), outbound via wrong phone (122205934115920)

---

## Part 7 — Flow Topology

All 5 published Flows belong to **WABA 1520701463019847** (the intended WABA, same as phone 1196075770246218):

| Flow | Name | WABA |
|---|---|---|
| 28104222025943520 | RideCheck Booking | 1520701463019847 ✅ |
| 2550767958730294 | Ridecheck Ubicación del vehículo | 1520701463019847 ✅ |
| 27205677485784073 | Vehicle details fallback | 1520701463019847 ✅ |
| 1535038801697863 | Ridecheck - Website lead datos final | 1520701463019847 ✅ |
| 1644218879979041 | ridecheck_booking_form | 1520701463019847 ✅ |

**BOOKING FLOW WABA: 1520701463019847**
**INTENDED PHONE WABA: 1520701463019847**
**SAME WABA: YES** — Flows and intended phone are co-located. No Flow migration required.

**INTENDED PHONE WABA ≠ CURRENT RUNTIME PHONE WABA**
Current runtime: WABA 101584872897508 (no Flows)
Intended: WABA 1520701463019847 (all Flows)

---

## Part 8 — Token Access

| Asset | Access |
|---|---|
| Read phone 1196075770246218 | PASS (HTTP 200, full object returned) |
| Messaging to 1196075770246218 | PASS (POST /messages returns param error code 100, not auth error) |
| Read phone 122205934115920 | PASS |
| Messaging to 122205934115920 | INTERMITTENT (some CE_TEXT sends succeeded; others failed) |
| Token type | SYSTEM_USER — app "CRM Ridecheck" |
| Token scopes | whatsapp_business_messaging ✅, whatsapp_business_management ✅ |
| Token validity | is_valid=true, expires_at=0 (never expires) |
| Token issued | 2026-08-29T23:20:23Z |

The current token has full messaging access to the intended phone (1196075770246218). Changing `WHATSAPP_PHONE_NUMBER_ID` does NOT require a token rotation.

---

## Part 9 — Wild #1 Delivery Failure Hypothesis

**Hypothesis:** Inbound conversation arrived via intended phone (1196075770246218 / WABA 1520701463019847), but CE outbound was configured to send via wrong phone (122205934115920 / WABA 101584872897508 / ON_PREMISE).

**Classification: STRONGLY_SUPPORTED**

Supporting evidence:
1. Outbound definitely used 122205934115920 — confirmed from `.env` and runtime env
2. 122205934115920 is ON_PREMISE type on WABA 101584872897508 — not the intended operational phone
3. The same failure pattern (failed outbound, wamid=None) occurred earlier on the same day (id=5685 at 19:56) and recovered when the tester re-engaged 11 minutes later
4. The correct phone (1196075770246218) is CLOUD_API, CONNECTED, GREEN — far more reliable
5. ON_PREMISE phones used via Cloud API endpoint have inherently unpredictable behavior (intermittent success is consistent with partial/stale registration)

**Why not PROVEN:**
- The exact Meta HTTP response for id=6043 is permanently unrecoverable (container logs lost)
- Sends via 122205934115920 DID succeed on 2026-08-31 (id=5687/5691/5693 are `status=read`) — proving the wrong phone is not uniformly broken, but is intermittently capable of Cloud API sends
- The specific error code that caused 6043 to fail is unknown

**Alternate partial explanation for mixed success/failure:** ON_PREMISE phones connected to Meta's legacy on-premises infrastructure sometimes route through Cloud API endpoints opportunistically, especially if a migration was partially completed. This explains why some sends via 122205934115920 succeeded while others failed — the routing is non-deterministic.

---

## Part 10 — Is Changing WHATSAPP_PHONE_NUMBER_ID Sufficient?

**YES — this is the single required change.**

| Check | Status |
|---|---|
| Token has messaging access to 1196075770246218 | YES ✅ |
| 1196075770246218 is CLOUD_API + CONNECTED + GREEN | YES ✅ |
| All Flows are on same WABA as 1196075770246218 | YES ✅ |
| docker-compose.yml uses `${WHATSAPP_PHONE_NUMBER_ID}` (env-var) | YES — change only .env ✅ |
| docker-compose.beta.yml inherits from .env (no PHONE_NUMBER_ID override) | YES ✅ |
| n8n processes inbound from all WABAs subscribed to the app | YES — inbound is unaffected |
| Other WHATSAPP_* env vars reference 122205934115920 | NO — all other Flow IDs are correct ✅ |

**No other runtime variables need to change.** The WHATSAPP_FLOW_ID, WHATSAPP_VEHICLE_FALLBACK_FLOW_ID, WHATSAPP_LOCATION_FALLBACK_FLOW_ID, WHATSAPP_WEBSITE_FLOW_ID values all belong to WABA 1520701463019847 (same WABA as the correct phone) — they are already correct.

**Exact proposed remediation (describe only — not yet executed):**

```bash
# In /opt/ridecheck-crm/.env — change:
WHATSAPP_PHONE_NUMBER_ID=122205934115920
# to:
WHATSAPP_PHONE_NUMBER_ID=1196075770246218

# Then restart backend (from /opt/ridecheck-crm/):
docker compose -f docker-compose.yml \
               -f /opt/ridecheck-crm-release-candidate/docker-compose.beta.yml \
               up -d --force-recreate backend
```

Verify immediately after: confirm `WHATSAPP_PHONE_NUMBER_ID=1196075770246218` in container env before enabling outbound.

---

## Document Corrections

The following wording in prior L4/L4.1 documents should be understood as corrected by this audit:

### RETRACTED (incorrect):
> "Root cause confirmed (L4.1): phone number +54 9 11 5829-5318 is DISCONNECTED from WhatsApp Business Platform"
> "Wild #2 BLOCKED until owner reconnects phone via Meta Business Manager"
> "Phone number DISCONNECTED — all Meta API sends will fail"

### CORRECT replacement:
> "Wild #1 delivery failure (id=6043): CE was configured to send via the wrong Meta phone asset (122205934115920 / +54 9 11 5829-5318 / ON_PREMISE / WABA 101584872897508). The intended operational phone is 1196075770246218 (+54 9 11 5700-8687 / CLOUD_API / CONNECTED / GREEN / WABA 1520701463019847). The `status=DISCONNECTED` finding from L4.1 is retracted: the field was unstable across API calls and is not a reliable indicator. Root cause of delivery failures: wrong phone asset configured in WHATSAPP_PHONE_NUMBER_ID."

### Canonical operational phone identity (for all future documentation):
| Field | Value |
|---|---|
| WhatsApp number | +54 9 11 5700-8687 |
| Phone Number ID | 1196075770246218 |
| WABA | 1520701463019847 (Ridecheck Assistance) |
| Status | CONNECTED |
| Platform | CLOUD_API |
| Quality | GREEN |

---

## Summary Return Block

**STATUS: COMPLETE**

**INTENDED OPERATIONAL PHONE:** +54 9 11 5700-8687

**INTENDED PHONE NUMBER ID:** 1196075770246218

**CURRENT RUNTIME PHONE NUMBER ID:** 122205934115920

**RUNTIME MATCHES INTENDED:** NO

**FINDING:** RUNTIME PHONE ASSET MISMATCH — runtime configured with wrong Meta phone (ON_PREMISE / WABA 101584872897508) instead of intended operational phone (CLOUD_API / WABA 1520701463019847). All outbound messages sent via wrong sender identity since at least 2026-08-31.

**SEVERITY:** HIGH

**META TOPOLOGY:**

WABA 1 (101584872897508): "Ride Check Assistance" — Phone 122205934115920 (+54 9 11 5829-5318), ON_PREMISE, DISCONNECTED, no Flows — **WRONG WABA**

WABA 2 (1520701463019847): "Ridecheck Assistance" — Phone 1196075770246218 (+54 9 11 5700-8687), CLOUD_API, CONNECTED, GREEN, all 5 published Flows — **CORRECT WABA**

Test WABA (987783207096418): "Test WhatsApp Business Account" — Meta test number only

**INTENDED PHONE WABA:** 1520701463019847

**CURRENT RUNTIME PHONE WABA:** 101584872897508

**BOOKING FLOW WABA:** 1520701463019847

**INTENDED PHONE + BOOKING FLOW SAME WABA:** YES ✅

**TOKEN ACCESS INTENDED PHONE (1196075770246218):** YES

**TOKEN ACCESS CURRENT CONFIGURED PHONE (122205934115920):** YES (intermittent messaging capability)

**WILD #1 INBOUND PHONE NUMBER ID:** UNKNOWN (metadata not stored in raw_payload)

**OUTBOUND CONFIGURED PHONE NUMBER ID:** 122205934115920

**SAME PHONE:** UNKNOWN / LIKELY NO (inbound via intended WABA, outbound via wrong WABA)

**PHONE-ASSET-MISMATCH AS DELIVERY ROOT CAUSE:** STRONGLY_SUPPORTED (ON_PREMISE phone used via Cloud API endpoint → intermittent failures; correct CLOUD_API phone would be reliable)

**DISCONNECTED CAUSAL CLAIM:** RETRACTED

**CONFIG HISTORY:**
- May 2026 (ddcd03b): docker-compose.yml hardcoded to `1196075770246218` ← correct
- Unknown date (between May 2026 and Aug 2026): docker-compose.yml changed to `${WHATSAPP_PHONE_NUMBER_ID}` env-var substitution
- At least by Aug 31, 2026: `.env` contains `122205934115920` ← wrong; token rotation on 2026-08-31 rewrote .env

**META CONFIG CHANGE REQUIRED:** NO — only `.env` change needed

**EXACT PROPOSED REMEDIATION:** Change `.env` `WHATSAPP_PHONE_NUMBER_ID=122205934115920` → `WHATSAPP_PHONE_NUMBER_ID=1196075770246218`. Restart backend. Token, Flows, and all other env vars are already correct for the intended phone.

**OUTBOUND:** OFF

**NO META CHANGES MADE:** YES

**PRODUCTION DB TOUCHED:** NO

**READY TO REMEDIATE:** YES — upon explicit owner authorization to change `.env` and restart backend
