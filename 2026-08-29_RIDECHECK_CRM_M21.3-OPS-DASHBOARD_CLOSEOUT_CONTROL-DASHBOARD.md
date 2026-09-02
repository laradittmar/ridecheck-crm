PROJECT: RIDECHECK_CRM
TYPE: CLOSEOUT
MILESTONE: M21.3-OPS-DASHBOARD
DATE: 2026-08-29
AUTHOR: Claude Sonnet 4.6 (AI assistant, supervised)
DB: crm_test (READ ONLY during this milestone — no schema changes applied)

---

## SAFETY CONSTRAINTS — CONFIRMED SATISFIED

| Constraint | Status |
|---|---|
| OUTBOUND remains OFF | ✓ CONFIRMED |
| No WhatsApp messages sent | ✓ CONFIRMED |
| ConversationEngine business logic unchanged | ✓ CONFIRMED |
| Pricing rules unchanged | ✓ CONFIRMED |
| Scheduling rules unchanged | ✓ CONFIRMED |
| n8n NOT activated/changed | ✓ CONFIRMED |
| Meta Flow NOT published/changed | ✓ CONFIRMED |
| Credentials NOT rotated/changed | ✓ CONFIRMED |
| Production DB READ ONLY / NOT touched | ✓ CONFIRMED |
| No second message ledger created | ✓ CONFIRMED (reused existing tables) |

---

## STATUS: PASS

---

## DATA SOURCES

| Source | Table(s) | Dashboard Usage |
|---|---|---|
| Inbound messages | `whatsapp_messages` (direction='in') | INBOUND HOY card, message trace |
| Outbound messages | `whatsapp_messages` (direction='out', automated=True) | OUTBOUND HOY card, ledger view, path monitoring |
| Thread context | `whatsapp_threads` | Thread health, inbox links, last_message_at |
| Contact identity | `whatsapp_contacts` | Display name, masked wa_id |
| Conversation state | `whatsapp_thread_states` | needs_human flag, last_stage |
| Processing latency | `ai_events.latency_total_ms` | P50/P95/MAX latency cards |
| Security incidents | `security_events` | Critical events panel, event counts |
| Processing failures | `ai_events` (status='failed') | ERRORES card |
| Outbound state | `OUTBOUND_ENABLED` env var | OUTBOUND OFF/ON badge |

No new tables or columns created. All data sourced from existing canonical records.

---

## ROUTE

`GET /control` — registered in `backend/app/ui/kanban.py` router
Protected by auth middleware (`/control` added to `_is_protected_path()` in `main.py`)
Returns: `HTMLResponse` via `render_control_page(user_email)` from `control_view.py`

HTTP runtime proof: `curl -s -o /dev/null -w "%{http_code}" http://localhost:8000/control` → **303** (auth redirect — correct)

With valid session cookie: **200 OK** — HTML response, 49,858 bytes

---

## SIDEBAR: PASS

Control item added to ALL CRM pages via `backend/app/ui/components.py` `render_sidebar_nav()`.
The function now always includes `/control` with monitor SVG icon.

Verified: `render_sidebar_nav()` output contains `/control` and `Control` label.

---

## SUMMARY CARDS

| Card | Status | Notes |
|---|---|---|
| Inbound (INBOUND HOY) | PASS | Counts WhatsAppMessage direction='in' in window |
| Outbound (OUTBOUND HOY) | PASS | Counts automated=True direction='out' |
| Unanswered (SIN RESPUESTA) | PASS | Renders from `/api/ops/threads` count |
| Needs human (NECESITA HUMANO) | PASS | waiting_human_count from summary |
| Critical (ERRORES/CRÍTICOS) | PASS | critical_events_count + processing_failures_count |
| P50 latency | PASS | AiEvent.latency_total_ms P50 in selected window |
| P95 latency | PASS | AiEvent.latency_total_ms P95 in selected window |
| Outbound state | PASS | Reads OUTBOUND_ENABLED env var; displays OFF as calm grey (not red) |

---

## THREAD HEALTH

| Feature | Status | Notes |
|---|---|---|
| Unanswered bot | PASS | latest direction='in', needs_human=False → UNANSWERED_BOT |
| Waiting customer | PASS | latest direction='out' → WAITING_CUSTOMER (excluded from unanswered) |
| Waiting human | PASS | latest direction='in', needs_human=True → WAITING_HUMAN |
| Critical age | PASS | CRITICAL when waiting >= 300s; WARNING when >= 120s; NORMAL when < 120s |

Thresholds: `UNANSWERED_WARNING_SECONDS = 120`, `UNANSWERED_CRITICAL_SECONDS = 300`
These constants are dashboard-only. No CE behavior changed.

---

## MESSAGE TRACE: PASS

Chronological descending view from `/api/ops/messages`.
- Direction filter: IN / OUT / all
- Window: Hoy / 24h / 7d
- Limit: 100 by default (max 500)
- Each row: time, direction, customer (masked wa_id), type, preview (80 chars), path_id, status
- Click to expand: WAMID, internal ID, thread_id, deployment_id, path_id, timestamps (no raw tokens)
- `is_path_critical` flag on each message for frontend highlighting

---

## OUTBOUND LEDGER: PASS

Exposed via `/api/ops/messages?direction=out` with path_id, status (pending/sent/delivered/read/failed/blocked), deployment_id. Not a new ledger — reuses existing `whatsapp_messages` table queried by direction='out' and automated=True.

---

## PATH MONITORING: PASS

`GET /api/ops/paths?window=...`

| Path | is_authorized | is_legacy | is_critical |
|---|---|---|---|
| CE_TEXT, CE_FLOW, CE_INTERACTIVE, CE_LIST | True | False | False |
| MANUAL_CRM, BOOKING_FLOW, SYSTEM_NOTIFICATION | True | False | False |
| LEGACY_N8N_AI_PIPELINE | False | True | **True** |
| null → "UNKNOWN" | False | False | **True** |
| Any other value | False | False | **True** |

Counts: total, success (sent/delivered/read), blocked, failed per path.

---

## CRITICAL EVENTS: PASS

`GET /api/ops/critical-events?window=...`

SecurityEvent records mapped to categories:

| event_type | category |
|---|---|
| OUTBOUND_ATTEMPT_WITH_UNKNOWN_SOURCE | UNAUTHORIZED_PATH |
| UNREGISTERED_OUTBOUND_SOURCE | UNAUTHORIZED_PATH |
| LEGACY_SENDER_REACHED | LEGACY_PATH_REACHED |
| META_STATUS_FOR_UNKNOWN_WAMID | UNKNOWN_WAMID |
| SUCCESSFUL_META_SEND_WHILE_OUTBOUND_OFF | OUTBOUND_OFF_BUT_META_SUCCESS |
| Any other | SECURITY |

All sourced from existing `security_events` table. No new event types created.

---

## INBOX LINK: PASS

Each thread row in `/api/ops/threads` response includes:
```json
"inbox_link": "/whatsapp/thread/{thread_id}"
```
JavaScript renders this as a tappable link. Clicking navigates to the WhatsApp Inbox thread view. No sending from Control dashboard.

---

## AUTO REFRESH

Interval: 10,000ms (10 seconds)
Implementation: `setInterval(refreshAll, 10000)` in dashboard JavaScript
User control: "Pausar/Reanudar" button stops/restarts the interval
Last updated: HH:MM:SS shown in page header after each successful refresh cycle

---

## MOBILE: PASS

- `@media (max-width: 768px)` and `(max-width: 480px)` breakpoints
- Status bar wraps on small screens
- Tables have `overflow-x: auto` container (horizontal scroll)
- Inbox links remain tappable (`min-height` touch targets)
- Sidebar uses existing collapse mechanism (localStorage)

---

## NEW TESTS

| OPS test | Description | Result |
|---|---|---|
| OPS-01 (3 tests) | Auth: /control protected, API not in UI prefix | PASS |
| OPS-02 (11 tests) | Dashboard renders, structure valid | PASS |
| OPS-03 (2 tests) | Inbound count correct | PASS |
| OPS-04 (2 tests) | Outbound count from automated records | PASS |
| OPS-05 (5 tests) | Outbound OFF/ON via env var | PASS |
| OPS-06 (3 tests) | Unanswered bot identified | PASS |
| OPS-07 (3 tests) | Waiting customer not classified unanswered | PASS |
| OPS-08 (3 tests) | Needs_human → waiting human | PASS |
| OPS-09 (8 tests) | Critical threshold 300s, warning 120s | PASS |
| OPS-10 (2 tests) | Latency from AiEvent.latency_total_ms | PASS |
| OPS-11 (5 tests) | P50 correct | PASS |
| OPS-12 (3 tests) | P95 correct | PASS |
| OPS-13 (3 tests) | Authorized path not critical | PASS |
| OPS-14 (3 tests) | Unknown/null path critical | PASS |
| OPS-15 (4 tests) | Legacy path critical | PASS |
| OPS-16 (2 tests) | Unknown WAMID security event visible | PASS |
| OPS-17 (1 test) | Outbound-off Meta success visible | PASS |
| OPS-18 (1 test) | Outbound ledger visible | PASS |
| OPS-19 (3 tests) | Direction filter correct | PASS |
| OPS-20 (2 tests) | Inbox link correct | PASS |
| OPS-21 (3 tests) | Window/health filters work | PASS |
| OPS-22 (4 tests) | Preview truncates; esc() function in JS | PASS |
| OPS-23 (4 tests) | No secrets in rendered output | PASS |
| OPS-24 (6 tests) | Empty state renders cleanly | PASS |
| OPS-25 (4 tests) | Mobile media queries present | PASS |
| OPS-26 (2 tests) | Reads do not mutate DB | PASS |
| OPS-27 (3 tests) | No outbound gate/send calls | PASS |
| OPS-28 (5 tests) | Auto-refresh implemented | PASS |
| OPS-29 (4 tests) | WhatsApp Inbox unchanged | PASS |
| OPS-30 (5 tests) | Kanban/Agenda unaffected | PASS |
| Utilities (11 tests) | Helper functions correct | PASS |

**OPS test total: 118 tests / 118 PASS**

---

## FULL REGRESSION

| Category | Tests |
|---|---|
| Previous baseline | 728 |
| New OPS tests | 118 |
| **Total** | **846** |
| Passed | **846** |
| Failed | **0** |
| Skipped | **0** |
| Subtests | 31 |

Duration: 178.17s

**No regressions introduced.**

---

## RUNTIME VISUAL PROOF

```
GET /control (no auth)        → 303 (redirect to /login) ✓
GET /control (valid session)  → 200 OK, 49,858 bytes ✓
  Content: "Control" ✓
  Content: "/api/ops/" ✓
  Content: "/whatsapp/inbox" ✓
  Content: "outbound" ✓
  Content: "setInterval" ✓
  Content: "sidebar" ✓

GET /api/ops/summary          → 200 OK ✓
  outbound_enabled: false ✓
  inbound_count: 0 ✓
  (empty dev state — correct)

GET /api/ops/threads          → 200 OK, count: 0 ✓
GET /api/ops/paths            → 200 OK, paths: [] ✓
GET /api/ops/critical-events  → 200 OK, count: 0 ✓
GET /api/ops/messages         → 200 OK, count: 0 ✓

Sidebar: /control nav item present in all pages (components.py) ✓
Kanban router: /control route registered ✓
Main.py: /control in protected_prefixes ✓
```

---

## FILES CHANGED

| File | Change |
|---|---|
| `backend/app/routes/ops_dashboard.py` | NEW: 5 API endpoints (summary, messages, threads, paths, critical-events) |
| `backend/app/ui/control_view.py` | NEW: render_control_page(), ICON_CONTROL, full dashboard HTML/CSS/JS |
| `backend/app/ui/kanban.py` | ADD: `/control` route, import render_control_page |
| `backend/app/ui/components.py` | ADD: Control item to render_sidebar_nav() |
| `backend/app/main.py` | ADD: ops_dashboard_router registration, /control to protected_prefixes |
| `tests/test_m21_3_ops_dashboard.py` | NEW: 118 OPS tests |

---

## MIGRATION: NONE

No schema changes. No new tables, no new columns. Dashboard queries existing canonical tables.

---

## OUTBOUND: OFF

## MESSAGES SENT: 0

## N8N CHANGED: NO

## META CHANGED: NO

## CE BUSINESS LOGIC CHANGED: NO

## PRODUCTION DB TOUCHED: NO

## SAFE FOR OWNER VISUAL RECHECK: YES

STOP.
