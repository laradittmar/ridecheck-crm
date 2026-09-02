PROJECT: RIDECHECK_CRM
TYPE: CLOSEOUT
MILESTONE: M21.3-C-D
DATE: 2026-08-28
BRANCH: main

---

## Scope

M21.3-C-D implements the complete backend for the RideCheck Booking Meta Flow
(Flow ID: 28104222025943520, status: DRAFT). The backend handles the
encrypted Data Exchange lifecycle — from initial APPOINTMENT screen through
SUMMARY confirmation to atomic booking creation — without sending any
WhatsApp message or touching production infrastructure.

---

## Regression Gate

```
platform linux -- Python 3.12.13, pytest-9.1.1
rootdir: /tmp/rctest3

tests/test_m19_r1_outbound_safety_gate.py    15 passed
tests/test_m19_f2_2_outbound_kill_switch.py  26 passed
tests/test_m20_4_3_blocked_dispatch.py        9 passed (+ 18 subtests)
tests/test_m2_authorized_paths.py            28 passed
tests/test_m21_3_hardening_final.py          25 passed
tests/test_m21_3_c_d_booking_flow.py         47 passed

TOTAL: 150 passed, 0 failed, 0 skipped — 170.84s (0:02:50)
```

---

## Deliverables

### 1 — `backend/app/services/booking_flow_service.py` (new)

Core service implementing the full Data Exchange lifecycle.

| Method | Action |
|--------|--------|
| `handle_init(token)` | Returns APPOINTMENT screen with available dates (14-day horizon) |
| `handle_date_selected(token, date)` | Returns updated time slot list for chosen date |
| `handle_prepare_summary(token, data)` | Validates inputs; returns SUMMARY screen with server-generated summaries |
| `handle_confirm_booking(token, data)` | Revalidates slot → advisory lock → atomic booking creation → SUCCESS |
| `resolve_context(token)` | Validates token age (≤2h), DB match, not-consumed; returns `BookingContext` |

Additional exports:
- `decrypt_flow_request(body)` — RSA-OAEP → AES key; AES-128-GCM → plaintext payload
- `encrypt_flow_response(response, aes_key, iv)` — AES-128-GCM with IV flipped (XOR 0xFF)
- `make_booking_token(thread_id)` / `parse_booking_token(token)` — opaque token helpers
- `health_response()` — standard Meta Flow ping response
- `BOOKING_HORIZON_DAYS = 14` — centralized constant

#### Booking creation contract (matches CE `_process_flow_response`)

```
ThreadRevision(status="booked", buyer_name, buyer_phone, buyer_email,
               seller_name, address, scheduled_date, scheduled_time,
               tipo_vehiculo, marca, modelo, anio, publication_url,
               zone_group, appointment_approval_status="PENDING",
               appointment_approval_token=<urlsafe-token>)

Revision(lead_id, tipo_vehiculo, marca, modelo, anio,
         zone_group, zone_detail, direccion_texto,
         vendedor_tipo, tipo_vendedor, turno_fecha, turno_hora)

lead.estado = "COORDINAR_DISPONIBILIDAD"
lead.flag   = "ACEPTADO"
lead.necesita_humano = True
lead.nombre / apellido  ← populated from buyer name if blank

state.current_revision_id = thread_rev.id
state.last_stage          = "BOOKED"
state.needs_human         = True
state.flow_booking_token  = None   ← token consumed
```

#### PostgreSQL advisory lock

`pg_try_advisory_xact_lock(lock_key)` where `lock_key` is a deterministic 31-bit hash
of the ISO date string. Prevents two concurrent confirmations from double-booking
the same date slot. On SQLite (tests) the `pg_try_advisory_xact_lock` call raises an
OperationalError which is caught and silently skipped — SQLite serializes writes natively.

#### Token format and validation

`"{thread_id}-{timestamp}-{nonce}"` — opaque to the Flow user.

Validation chain:
1. Must parse as `thread_id` (int) + `issued_at` (int)
2. `now - issued_at ≤ TOKEN_MAX_AGE_SECONDS` (7200 s = 2 h)
3. DB lookup: `WhatsAppThreadState.flow_booking_token == token`
4. Consumed token check: field must not be None

#### ScheduleCheckIn address placeholder

`ScheduleCheckIn.address` requires `min_length=1`. The date/slot availability
lookups (`_available_dates`, `_slots_for_date`) pass `address="-"` since zone-based
availability is driven by `zone_group`, not the physical address.

### 2 — `backend/app/routes/flow_data_exchange.py` (new)

Route: `POST /integrations/whatsapp/flows/booking/data-exchange`

| Request action | Response |
|----------------|----------|
| `ping` | `{"version":"3.0","data":{"status":"active"}}` (encrypted) |
| `INIT` | APPOINTMENT screen data (encrypted) |
| `data_exchange` + `date_selected` | APPOINTMENT screen with time slots (encrypted) |
| `data_exchange` + `prepare_summary` | SUMMARY screen data (encrypted) |
| `data_exchange` + `confirm_booking` | SUCCESS screen or conflict APPOINTMENT screen (encrypted) |

All responses are `application/octet-stream` (AES-128-GCM encrypted).

Error handling:
- `decrypt_flow_request` failure → HTTP 421 (not 500, to avoid leaking crypto state)
- `BookingSlotConflictError` → encrypted conflict APPOINTMENT screen (not HTTP error)
- `BookingTokenError` → encrypted error APPOINTMENT screen (session expired)
- Unknown action/trigger → HTTP 422

The endpoint is registered as a **public path** (no session auth required):
```python
public_paths = (
    "/integrations/whatsapp/webhook",
    "/integrations/whatsapp/flows/booking/data-exchange",
)
```

### 3 — `backend/app/main.py` (modified)

- Import: `from .routes.flow_data_exchange import router as flow_data_exchange_router`
- Registration: `app.include_router(flow_data_exchange_router)`
- Public path: added `/integrations/whatsapp/flows/booking/data-exchange` to `_is_public_path()`

### 4 — `backend/app/settings.py` (modified, from prior session)

```python
# In Settings dataclass:
booking_flow_id: str = ""                  # WHATSAPP_BOOKING_FLOW_ID
flow_booking_private_key_path: str = ""    # FLOW_BOOKING_PRIVATE_KEY_PATH

# In get_settings():
booking_flow_id=_getenv("WHATSAPP_BOOKING_FLOW_ID", "28104222025943520"),
flow_booking_private_key_path=_getenv("FLOW_BOOKING_PRIVATE_KEY_PATH"),
```

### 5 — `backend/requirements.txt` (modified, from prior session)

`cryptography==42.0.8` — required for RSA-OAEP + AES-128-GCM (Meta Flow Data Exchange protocol).
Already installed in container via `pip install cryptography==42.0.8`.

### 6 — `tests/test_m21_3_c_d_booking_flow.py` (new)

47 tests across BF01–BF30:

| Group | IDs | Coverage |
|-------|-----|----------|
| Appointment screen | BF01–BF10 | init, date items, slot items, horizon boundary, Spanish titles, slot passthrough |
| Context/token validation | BF11–BF15 | missing token, expired token, tampered token, consumed token, summary field validation |
| Revalidation + concurrency | BF16–BF18 | ScheduleService.check() called, conflict raises, advisory lock SQLite skip |
| Booking creation | BF19–BF24 | ThreadRevision.status, buyer fields, lead.estado, lead.flag+human, Revision schedule+vehicle, token consumed, second-confirm rejected, state.last_stage |
| Crypto + invariants | BF25–BF30 | AES-GCM roundtrip, encrypted≠plaintext, decrypt validation, opaque output, SUCCESS screen, token format, OUTBOUND OFF |

---

## Owner Setup: Private Key

To enable actual Meta Flow data decryption in production, the RSA private key
(generated during Flow publish setup) must be made available:

```bash
# 1. Place the PEM file on the server (never commit it to git)
scp /local/path/flow_booking_private_key.pem root@<server>:/opt/ridecheck-secrets/

# 2. Set the environment variable in .env (gitignored)
FLOW_BOOKING_PRIVATE_KEY_PATH=/opt/ridecheck-secrets/flow_booking_private_key.pem

# 3. Restart the backend container
docker compose restart backend
```

The path is read at request time (not cached at startup), so the file can be
rotated without a restart as long as the path stays the same.

---

## Flow UX Contract

| Screen | Trigger that leads to it | Server-side data |
|--------|--------------------------|------------------|
| APPOINTMENT | INIT | date list (14-day horizon, slots > 0) + booking_token + vehicle_summary + location_summary |
| APPOINTMENT | date_selected | above + time list for selected date |
| SUMMARY | prepare_summary | appointment_summary, customer_summary, all user-entered fields echoed server-side |
| SUCCESS | confirm_booking (valid) | extension_message_response with flow_token |
| APPOINTMENT (conflict) | confirm_booking (slot gone) | refreshed date/time list + slot_conflict_message |

---

## Security Invariants (Unchanged)

- **OUTBOUND REMAINS OFF** — `BookingFlowService.handle_confirm_booking()` creates
  no `WhatsAppMessage` records. Verified by BF30.
- **Flow NOT published** — Flow ID 28104222025943520 remains in DRAFT status.
- **Endpoint NOT connected** — the data-exchange route is not registered in Meta
  Business Manager. No real WhatsApp users can reach it.
- **Private key NOT committed** — `FLOW_BOOKING_PRIVATE_KEY_PATH` points to a
  file-system secret; the PEM is never in git.
- **Token consumed on booking** — `flow_booking_token` is set to NULL after
  `handle_confirm_booking()` succeeds. Replay attacks rejected by BF23.
- **DB before commit** — `thread_rev`, `crm_rev`, and `lead` mutations are
  flushed and committed atomically in a single `db.commit()`.

---

## Standing Constraints (Unchanged)

- OUTBOUND MUST REMAIN OFF
- Production not touched
- n8n not modified
- No Meta token rotation without owner authorization
- No Meta Business Manager changes
- crm_test only for DB work
- No Flow publication, endpoint registration, public key upload, or live send

---

## New Files

| File | Purpose |
|------|---------|
| `backend/app/services/booking_flow_service.py` | Core booking flow service — all Data Exchange handlers |
| `backend/app/routes/flow_data_exchange.py` | POST `/integrations/whatsapp/flows/booking/data-exchange` |
| `tests/test_m21_3_c_d_booking_flow.py` | BF01–BF30 test suite (47 tests) |

## Modified Files

| File | Change |
|------|--------|
| `backend/app/main.py` | Import + register `flow_data_exchange_router`; add public path |
| `backend/app/settings.py` | `booking_flow_id`, `flow_booking_private_key_path` settings |
| `backend/requirements.txt` | `cryptography==42.0.8` |
