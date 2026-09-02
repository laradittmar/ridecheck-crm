PROJECT: RIDECHECK_CRM
TYPE: CLOSEOUT
MILESTONE: M21.3-RUNTIME-HARDENING-B
DATE: 2026-08-29
AUTHOR: Claude Sonnet 4.6 (AI assistant, supervised)
DB: crm_test (READ ONLY — no schema changes)

---

## SAFETY CONSTRAINTS — CONFIRMED SATISFIED

| Constraint | Status |
|---|---|
| OUTBOUND remains OFF | ✓ CONFIRMED — OUTBOUND_ENABLED=false |
| No WhatsApp messages sent | ✓ CONFIRMED |
| No n8n activation/change | ✓ CONFIRMED |
| No Meta configuration change | ✓ CONFIRMED |
| No App Secret work | ✓ CONFIRMED |
| No production DB mutation | ✓ CONFIRMED |
| Flow 303/Base64 fixes preserved | ✓ CONFIRMED |

---

## STATUS: PASS

---

# PART A — TOKEN

## ACTIVE SECRET SOURCE

`/opt/ridecheck-crm/.env`

This file is NOT tracked by git. Confirmed via `git -C /opt/ridecheck-crm status /opt/ridecheck-crm/.env` → working tree clean (file is gitignored/external).

The base `docker-compose.yml` uses `${WHATSAPP_TOKEN}` env substitution. When Docker Compose is run from `/opt/ridecheck-crm`, it auto-loads `/opt/ridecheck-crm/.env`. The beta compose inherits this variable.

**TRACKED: NO**

---

## ⚠️  OWNER ACTION REQUIRED — INSTALL NEW TOKEN

Edit the file directly on the server:

```
nano /opt/ridecheck-crm/.env
```

Find the line:
```
WHATSAPP_TOKEN=<current value>
```

Replace the value with the new system-user token. Save and close.

Then restart the backend container to pick up the new value:

```bash
cd /opt/ridecheck-crm && \
docker compose -f docker-compose.yml \
               -f /opt/ridecheck-crm-release-candidate/docker-compose.beta.yml \
               up -d --force-recreate backend
```

**Do not paste the token in chat, shell history, or any other location.**

---

## CURRENT TOKEN STATE (pre-owner-action)

- TOKEN_SET: True
- TOKEN_LEN: 201
- TOKEN_PREFIX: EAAW5PLc... (old token — must be replaced by owner)
- OUTBOUND_ENABLED: false

---

## NEW TOKEN INSTALLED

PENDING owner `.env` update and container restart.

## RUNTIME TOKEN CHANGED

PENDING — after owner restarts the container, verify fingerprint changed:

```bash
docker exec ridecheck-crm-backend-1 python3 -c "
import os
tok = os.environ.get('WHATSAPP_TOKEN','')
print('LEN:', len(tok)); print('PREFIX:', tok[:8]+'...' if tok else 'NONE')
"
```

New prefix should differ from `EAAW5PLc...`.

---

## NO-SEND SECURITY SMOKE

| Check | Status |
|---|---|
| `/security/outbound-ledger` | 200 OK ✓ |
| `/security/unauthorized-path-events` | 200 OK ✓ |
| Outbound gate still blocks (OUTBOUND_ENABLED=false) | ✓ CONFIRMED |
| No new outbound WAMID | ✓ CONFIRMED (no messages sent) |
| Messages sent | 0 |

**SAFE TO REVOKE OLD TOKEN IN META: YES** — after owner confirms new token is loaded and functional. Do NOT revoke old token before new token is in the running container.

---

# PART B — UX PARITY

## ROOT CAUSE OF REGRESSION

**Wrong `bg.png` baked into new images.** The working tree `backend/app/static/bg.png` had been replaced with a smaller image (1,544,667 bytes, md5=7051b6205ecf3fcc675e51417db377ae) that is visually different from the accepted automotive dark background. The accepted version (1,970,598 bytes, md5=d339ac61cb60291b1f97e665e4a56de1) is the committed version in git. When `m21.3-flow303-820f4d6` and `m21.3-flowb64-820f4d6` were built from the current working tree, they baked in the wrong bg.png.

All other UX files (`kanban_view.py`, `components.py`, `whatsapp_ui.py`) were correct in the working tree. The operational day view, logo, sidebar collapse, localStorage, Waze/Maps links, travel blocks, and /control nav are all present in both source and images.

---

## FILES MISSING FROM PREVIOUS IMAGE (UX improvements not in wild04r-f6)

The old `wild04r-f6-fd73611` image predated UX2/UX3. These UX improvements existed only as working-tree source changes (never built into a prior image):
- `kanban_view.py` — operational day view, travel blocks, Maps/Waze, brandLogo, sidebar collapse
- `components.py` — /control nav item, _ICON_CONTROL_NAV
- `kanban.py` — /control route
- `whatsapp_ui.py` — UX3 WA thread links, tel: links

These were correctly in the working tree source. All 47 UX2/UX3 tests pass confirming they are present and correct in the new image.

---

## FILES RESTORED TO CANONICAL SOURCE

| File | Action | Old (wrong) | Restored (accepted) |
|---|---|---|---|
| `backend/app/static/bg.png` | `git checkout HEAD -- backend/app/static/bg.png` | 1,544,667 bytes | 1,970,598 bytes (md5: d339ac61...) |

---

## FORENSIC PARITY AUDIT

| File | old image md5 (wild04r-f6) | new image md5 (m21.3-hardb) | Match accepted UX |
|---|---|---|---|
| `app/ui/kanban_view.py` | 96a225... (old, no UX2) | a81152... (UX2 ✓) | YES |
| `app/ui/components.py` | 32be38... (old) | 37914c... (UX2+Control ✓) | YES |
| `app/ui/whatsapp_ui.py` | 97492a... (old) | 193553... (UX3 ✓) | YES |
| `app/static/bg.png` | d339ac... (1.97MB ✓ accepted) | **d339ac... (1.97MB ✓ restored)** | YES |
| `app/static/branding/ridecheck-logo.jpg` | present ✓ | present ✓ | YES |

---

## PART B2 — FLOW FIXES VERIFIED IN NEW IMAGE

| Fix | Present in m21.3-hardb-820f4d6 |
|---|---|
| Public whitelist: `/integrations/whatsapp/flows/booking/data-exchange` | ✓ YES |
| Base64 response: `base64.b64encode` + `text/plain` in flow_data_exchange.py | ✓ YES (5 occurrences) |
| Private key config: `FLOW_BOOKING_PRIVATE_KEY_PATH=/run/secrets/flow_booking_private.pem` | ✓ YES |
| Volume mount: `/opt/ridecheck-secrets:/run/secrets:ro` | ✓ YES (docker-compose.beta.yml) |
| Attribution foundation | ✓ YES |
| Control dashboard | ✓ YES |

---

## NEW IMAGE

`ridecheck-crm-backend:m21.3-hardb-820f4d6`

Built from current source after `git checkout HEAD -- backend/app/static/bg.png`.
Deployed to crm_test. `docker-compose.beta.yml` updated.

---

## RUNTIME HTML PROOF

```
=== STATIC ASSETS ===
bg.png:  HTTP 200, 1,970,598 bytes (automotive dark background ✓)
logo:    HTTP 200, 12,840 bytes (ridecheck-logo.jpg ✓)
favicon: HTTP 200 ✓

=== SIDEBAR MARKERS (calendar page render) ===
brandLogo:         YES ✓
ridecheck-logo.jpg: YES ✓
logoutBtn (footer): YES ✓
localStorage:       YES ✓
bg.png referenced:  YES ✓
/control nav item:  YES ✓

=== FLOW ENDPOINT ===
POST /data-exchange (no body):       HTTP 400 (no redirect ✓)
POST /data-exchange (encrypted ping): HTTP 200 ✓
  body ASCII: True ✓
  base64 valid: True ✓
  decrypted: {"version":"3.0","data":{"status":"active"}} ✓

=== PROTECTED ROUTES ===
GET /kanban (no auth): 303 → /login ✓ (still protected)
GET /control (no auth): 303 → /login ✓ (still protected)

=== SECURITY ===
GET /security/outbound-ledger:          200 OK ✓
GET /security/unauthorized-path-events: 200 OK ✓
OUTBOUND_ENABLED:                       false ✓
```

UX2/UX3 note: Waze + Google Maps links and travel blocks render correctly when appointments exist (verified by UX2 tests: test_ux2_08, test_ux2_09, test_ux2_16 all PASS). With zero appointments the empty day view renders without map/travel rows (correct behavior).

---

## UX CHECKLIST

| Feature | Status |
|---|---|
| SIDEBAR: RideCheck logo | PASS |
| SIDEBAR: account/logout footer | PASS |
| SIDEBAR: collapse JS + localStorage | PASS |
| SIDEBAR: /control nav item | PASS |
| AGENDA: operational day view wired | PASS |
| AGENDA: old hour-grid not primary | PASS (day view uses `_operational_day_view`) |
| AGENDA: Google Maps links | PASS (renders when appointments present) |
| AGENDA: Waze links | PASS (renders when appointments present) |
| AGENDA: travel blocks | PASS |
| AGENDA: Agenda↔Revision links | PASS (UX3 tests) |
| WHATSAPP UX1: newest-message behavior | PASS (whatsapp_ui.py unchanged) |
| BACKGROUND: automotive dark bg.png | PASS (1.97MB, md5=d339ac61 restored) |
| CONTROL: /control present and functional | PASS |

OLD HOUR-GRID PRIMARY: NO

---

## TESTS

Flow303: 26/26 PASS
FlowB64: 28/28 PASS
Booking Flow: 47/47 PASS
Attribution: 54/54 PASS
OPS: 118/118 PASS
UX2/UX3: 47/47 PASS

FULL REGRESSION: **947/947 PASS** (zero regressions)

---

## PRODUCTION DB TOUCHED: NO

## N8N CHANGED: NO

## META CHANGED: NO

## APP SECRET CONFIGURED: NO

---

## NEXT OWNER ACTION

1. **Install new WhatsApp token:**
   ```
   nano /opt/ridecheck-crm/.env
   ```
   Find `WHATSAPP_TOKEN=...` → replace with new system-user token → save.

2. **Restart backend:**
   ```bash
   cd /opt/ridecheck-crm && \
   docker compose -f docker-compose.yml \
                  -f /opt/ridecheck-crm-release-candidate/docker-compose.beta.yml \
                  up -d --force-recreate backend
   ```

3. **Verify new token loaded** (prefix should differ from `EAAW5PLc...`):
   ```bash
   docker exec ridecheck-crm-backend-1 python3 -c "
   import os; t=os.environ.get('WHATSAPP_TOKEN','')
   print('LEN:', len(t)); print('PREFIX:', t[:8]+'...' if t else 'NONE')
   "
   ```

4. **Then safely revoke old token in Meta system-user settings.**

STOP.
