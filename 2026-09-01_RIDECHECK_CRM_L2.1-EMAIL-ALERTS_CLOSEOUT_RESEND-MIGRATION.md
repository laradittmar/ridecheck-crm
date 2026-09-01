PROJECT: RIDECHECK_CRM
TYPE: CLOSEOUT
MILESTONE: L2.1-EMAIL-ALERTS
STATUS: PASS
PROVIDER: RESEND
EXISTING RESEND CONFIG REUSED: YES
SMTP REQUIRED: NO
SAFE TEST EMAIL SENT: YES
PROVIDER ACCEPTED: YES
OWNER RECEIVED: PENDING
TESTS: 15
RUNTIME IMAGE: ridecheck-crm-backend:l2.1-email-3131f88
OUTBOUND: OFF
PRODUCTION DB TOUCHED: NO
ROADMAP UPDATED: YES

---

# L2.1 — Email Alerts: Resend Migration Closeout

**Date:** 2026-09-01
**Commit:** `3131f88` feat(L2.1-EMAIL-ALERTS): replace SMTP with Resend for unanswered-thread alerts
**Image:** `ridecheck-crm-backend:l2.1-email-3131f88`
**OUTBOUND:** OFF
**Database:** crm_test only — production DB NOT touched

---

## Summary

The `unanswered_alert.py` SMTP path has been fully replaced with the existing Resend
infrastructure. The project already had `RESEND_API_KEY`, `INTERNAL_BOOKING_EMAIL_TO`,
and `INTERNAL_BOOKING_EMAIL_FROM` configured in `.env`. The `resend_email.py` module
already contained the canonical urllib Resend pattern. SMTP credentials (`SMTP_PASSWORD`)
are not configured and were not required.

Operational alert delivery is now functional without any credential action by the owner.

---

## Changes

### `backend/app/services/resend_email.py`

Added `send_unanswered_alert()` between `send_scheduling_handoff_notification()` and
`send_human_review_notification()`. Reuses the existing canonical urllib Resend pattern:

- Accepts: `api_key`, `from_email`, `to_email`, `thread_id`, `customer_name`, `threshold_minutes`, `reason`
- Returns: `bool` (True = provider accepted, False = explicit failure)
- Guards: returns False + ERROR log if `api_key` is empty; returns False + ERROR log if `to_email` is empty
- Error handling: HTTPError → logs status code + provider body; URLError → logs reason; unexpected → logs exception; all return False
- No secret values in logs: api_key Bearer token appears only in Authorization header, not in any log call

### `backend/app/services/unanswered_alert.py`

- Removed: `import smtplib`, `from email.message import EmailMessage`, `_ALERT_EMAIL` constant
- Replaced `_send_alert_email()` SMTP implementation with Resend delegation:
  - Reads `s.resend_api_key`, `s.internal_booking_email_to`, `s.internal_booking_email_from` from `get_settings()`
  - Logs `WARNING` and returns early (no crash) if either key or recipient is missing
  - Calls `send_unanswered_alert()` from `resend_email`
  - Logs `ERROR` if delivery fails (not silent)
- Preserves existing invocation sites: CE-SLA path (`reason="CE-SLA"`) and human-handoff path (`reason="HUMAN"`)
- No other behavior changed

---

## Test Results: 15/15 PASS

| Test ID | Description | Result |
|---|---|---|
| EMAIL-01 | Missing api_key → returns False | ✅ PASS |
| EMAIL-01b | Missing api_key → no network call | ✅ PASS |
| EMAIL-02 | Missing to_email → returns False | ✅ PASS |
| EMAIL-02b | Missing to_email → no network call | ✅ PASS |
| EMAIL-03 | Successful Resend 200 → returns True | ✅ PASS |
| EMAIL-03b | Request body contains correct recipient | ✅ PASS |
| EMAIL-03c | Subject contains thread ID | ✅ PASS |
| EMAIL-04 | Resend HTTP error → returns False, logs status | ✅ PASS |
| EMAIL-05 | Network/URLError → returns False, logs reason | ✅ PASS |
| EMAIL-06 | API key value absent from error logs | ✅ PASS |
| EMAIL-06b | API key value absent from success logs | ✅ PASS |
| EMAIL-07 | smtplib absent from unanswered_alert; send_unanswered_alert present | ✅ PASS |
| EMAIL-07b | _send_alert_email passes correct recipient to Resend | ✅ PASS |
| EMAIL-08 | Missing RESEND_API_KEY → WARNING log, no crash | ✅ PASS |
| EMAIL-09 | Missing INTERNAL_BOOKING_EMAIL_TO → WARNING log, no crash | ✅ PASS |

---

## Smoke Send

```
SMOKE_SEND_RESULT: True
PROVIDER_ACCEPTED: YES
```

- Provider: Resend
- From: `notificaciones@ridecheck.ar` (INTERNAL_BOOKING_EMAIL_FROM)
- To: `ridecheckassistance@gmail.com` (INTERNAL_BOOKING_EMAIL_TO)
- Thread: SMOKE-L2.1
- Reason: L2.1-SMOKE-TEST
- No key value printed. No secret in any log output.

Owner delivery confirmation: PENDING (owner to verify inbox receipt).

---

## Image Verification

```
resend_email.py send_unanswered_alert count: 1
unanswered_alert.py smtplib count: 0
unanswered_alert.py send_unanswered_alert count: 2
```

Source/image parity: ✅

---

## Roadmap Updates

| Entry | Before L2.1 | After L2.1 |
|---|---|---|
| Email unanswered alerts | NEEDS FIX BEFORE PUBLIC LAUNCH | PROVEN / internal alert delivery restored via Resend |
| L2 closed items #3 | SMTP chosen; credential gap operator action | L2.1: migrated to Resend; smoke proven |
| L2 exit criterion email | MET (SMTP chosen) | MET (Resend migration; 15/15 EMAIL tests PASS) |
| docker-compose.beta.yml | l2-transport-53b04e5 | l2.1-email-3131f88 |

---

## What Was NOT Changed

- Booking Flow behavior
- WhatsApp send logic
- Conversation Engine
- Semantic authority
- Pricing / scheduling rules
- n8n workflow activation
- Production DB or runtime
- OUTBOUND_ENABLED (remains false)

---

## Safety Constraints Honored

- OUTBOUND OFF throughout. No WhatsApp messages sent.
- Production DB not touched. crm_test only.
- No secrets printed. No API key values in any log or output.
- No customer emails sent. Internal recipient only.
- n8n not activated or deactivated.
- Booking Flow not altered.
- Semantic authority (L1) not reopened.

---

## Next

L2 is FROZEN. L2.1 is a sub-gate closeout; it does not change L2's FROZEN PASS status.

**L3 — Dirty-History Certification is NEXT.**
