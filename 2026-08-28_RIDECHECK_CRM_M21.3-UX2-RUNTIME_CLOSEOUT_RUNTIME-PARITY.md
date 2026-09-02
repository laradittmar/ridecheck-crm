PROJECT: RIDECHECK_CRM
TYPE: CLOSEOUT
MILESTONE: M21.3-UX2-RUNTIME

DATE: 2026-08-28
BRANCH: main

---

STATUS: PASS

---

ROOT CAUSE

The previous UX2 session was technically correct but incomplete in two ways:

1. **`kanban.py` was never copied to the running container.** The calendar route
   still used the original signature (no `schedule_svc`, no `thread_by_lead`),
   so `render_calendar_page` received no ScheduleService and fell back to the
   default Nord/09:00 day-start info — but more critically, the operational day
   view was still being rendered (because `kanban_view.py` WAS copied).

   However, the live browser showed the OLD hour-grid, which means the Python
   module reloading didn't occur.

2. **uvicorn runs without `--reload`.** All file copies took effect on the
   container filesystem, but the Python process retained its original in-memory
   module cache from startup. `import` statements were never re-executed, so
   the on-disk changes were invisible to the running application.

3. **`/app/app/static/branding/` directory did not exist in the container.**
   The `ridecheck-logo.jpg` asset was never created in the container, so every
   request to `/static/branding/ridecheck-logo.jpg` returned HTTP 404. Browsers
   show the `alt` text or a broken image box — which may have appeared similar
   to the old "RIDECHECK" text.

4. **JS navigation fallback still referenced `.calDaySlots`.** Line 4608 of
   `kanban_view.py` had a JS string literal fallback for out-of-range day
   navigation that used the old empty-grid markup. Fixed to use
   `agendaDayWrap` / `agendaEmpty` consistent with the operational view.

---

RUNNING ENVIRONMENT

Container: ridecheck-crm-backend-1
Image: ridecheck-crm-backend:wild04r-f6-fd73611
Created: 2026-08-27T19:29:30 UTC
Started (before fix): 2026-08-27T20:32:36 UTC — running 25 h without reload
DB: crm_test (PostgreSQL, ridecheck-crm-postgres-1, untouched)
RC SHA: 53a6291 (main, clean)
Running process: uvicorn app.main:app --host 0.0.0.0 --port 8000 (no --reload)

---

SOURCE/RUNTIME PARITY

Files different BEFORE fix (container vs RC):

| File | RC hash | Container hash | Match |
|------|---------|----------------|-------|
| `app/ui/kanban_view.py` | 74419fda | 74419fda | YES* |
| `app/ui/whatsapp_ui.py` | 19355307 | 19355307 | YES* |
| `app/services/schedule.py` | 4552fb19 | 4552fb19 | YES* |
| `app/ui/kanban.py` | bc992a24 | 4305643986 | **NO** |
| `app/static/branding/ridecheck-logo.jpg` | present | **missing** | NO |

*Files matched on disk but Python process had not reloaded them (no --reload).

---

FILES COPIED/UPDATED (this session)

| File | Action |
|------|--------|
| `app/ui/kanban.py` | `docker cp` → container (was missing) |
| `app/ui/kanban_view.py` | `docker cp` → container (JS fallback fix) |
| `app/app/static/branding/` | `docker exec mkdir -p` created in container |
| `app/static/branding/ridecheck-logo.jpg` | `docker cp` → container |
| Container | `docker restart ridecheck-crm-backend-1` (module reload) |

---

LIVE UI (HTTP / RENDERED HTML PROOF)

Background: PASS
— `/static/bg.png` returns HTTP 200, 1,544,667 bytes (owner asset confirmed)
— CSS contains `bg.png?v=1787950287` (mtime cache-buster active)

Logo: PASS
— `/static/branding/ridecheck-logo.jpg` returns HTTP 200, 12,840 bytes
— Rendered HTML contains `class="brandLogo"` and `ridecheck-logo.jpg`
— `>RIDECHECK<` text NOT present in rendered HTML

Account/footer: PASS
— Rendered HTML contains `sidebarFooter`, `logoutBtnCompact`, `action="/logout"`
— Expanded: user email + full logout button
— Collapsed: compact ✕ button (CSS-driven, no JS required)

Sidebar collapse: PASS
— `localStorage.setItem("sidebar_collapsed", ...)` present in all rendered views
— `DOMContentLoaded` restores state from localStorage
— Table view toggleSidebar now saves and restores state
— Toggle arrow flips via CSS: `.sidebar.collapsed .sidebarToggle svg { transform: scaleX(-1); }`

Collapse persistence: PASS
— All three views (Kanban, Calendar, WhatsApp) share the same
  `localStorage.getItem("sidebar_collapsed")` key and restore on load

Agenda operational Day view: PASS
— Rendered HTML contains `agendaDayWrap` (44 occurrences = 42 pre-rendered + initial + WA shell)
— `agendaDayStartBlock`, `INICIO` present in Day view
— Wednesday zero-zone `Melo y Panamericana` rendered when schedule_svc provided

Day start: PASS
— `agendaDayStartBlock` + `INICIO` label + zero-zone detail + start time visible

Address→Google Maps: PASS
— `google.com/maps/search/?api=1&query=` link present for appointments with address
— Address text visible (`Maipú 1234`, etc.)

Secondary GPS: PASS
— `agendaGpsBtn` (🧭 button) rendered
— `agendaGpsDropdown` with "Google Maps" and "Waze" links rendered

Waze: PASS
— `waze.com/ul?q=` URL present in GPS dropdown

Travel blocks: PASS
— `<div class="agendaTravelBlock">` rendered between Norte→CABA appointments
— "Necesario: 60 min" and "Disponible: X min" shown
— `agendaMargin-` class applied (ok/tight/conflict)

Gap/margin: PASS
— Trailing gap block (`agendaGapEnd`) rendered when free time remains after last appointment

Old hour-grid still primary Day view: NO
— `<div class="calDaySlots">` does NOT appear as an HTML element
— Only legacy CSS class definition `.calDaySlots { ... }` remains (unused)
— JS navigation fallback updated from calDaySlots to agendaEmpty

---

REGRESSION GATE

```
platform linux -- Python 3.12.13, pytest-9.1.1, pluggy-1.6.0
rootdir: /tmp/rctest3

tests/test_m19_r1_outbound_safety_gate.py    15 passed
tests/test_m19_f2_2_outbound_kill_switch.py  26 passed
tests/test_m20_4_3_blocked_dispatch.py        9 passed  (+ 18 subtests)
tests/test_m2_authorized_paths.py            28 passed
tests/test_m21_3_hardening_final.py          25 passed
tests/test_m21_3_c_d_booking_flow.py         47 passed
tests/test_m21_3_ux2.py                      21 passed
tests/test_m21_3_ux2_runtime.py              10 passed

TOTAL: 181 passed, 0 failed, 0 skipped — 170.19s (0:02:50)
```

---

RUNTIME-01  /calendar HTML contains agendaDayWrap: PASS
RUNTIME-02  No <div class="calDaySlots"> as primary renderer: PASS
RUNTIME-03  WA shell contains brandLogo: PASS
RUNTIME-04  Logout/account footer present: PASS
RUNTIME-05  Collapse localStorage JS present: PASS
RUNTIME-06  /static/branding/ridecheck-logo.jpg → HTTP 200: PASS
RUNTIME-07  /static/bg.png → HTTP 200, >1 MB: PASS
RUNTIME-08  Google Maps link for appointment address: PASS
RUNTIME-09  Waze link in GPS dropdown: PASS
RUNTIME-10  Travel block for Norte→CABA: PASS

---

MODIFIED FILES (cumulative UX2 + UX2-RUNTIME)

| File | Change |
|------|--------|
| `backend/app/ui/kanban_view.py` | Logo, footer, collapse, bg cache-buster, operational day view, JS fallback fix |
| `backend/app/ui/whatsapp_ui.py` | Logo img (replaces brandText) |
| `backend/app/ui/kanban.py` | Calendar route: ScheduleService + thread_by_lead |
| `backend/app/services/schedule.py` | `get_day_start_info()` method |

NEW FILES

| File | Purpose |
|------|---------|
| `tests/test_m21_3_ux2.py` | UX2-01–UX2-21 (21 tests) |
| `tests/test_m21_3_ux2_runtime.py` | RUNTIME-01–RUNTIME-10 (10 tests) |

CONTAINER OPERATIONS (cumulative)

| Operation | Command |
|-----------|---------|
| Copy bg.png | `docker cp backend/app/static/bg.png ridecheck-crm-backend-1:/app/app/static/bg.png` |
| Copy kanban_view.py | `docker cp backend/app/ui/kanban_view.py ridecheck-crm-backend-1:/app/app/ui/kanban_view.py` |
| Copy whatsapp_ui.py | `docker cp backend/app/ui/whatsapp_ui.py ridecheck-crm-backend-1:/app/app/ui/whatsapp_ui.py` |
| Copy schedule.py | `docker cp backend/app/services/schedule.py ridecheck-crm-backend-1:/app/app/services/schedule.py` |
| Copy kanban.py | `docker cp backend/app/ui/kanban.py ridecheck-crm-backend-1:/app/app/ui/kanban.py` |
| Create branding dir | `docker exec ridecheck-crm-backend-1 mkdir -p /app/app/static/branding` |
| Copy logo | `docker cp backend/app/static/branding/ridecheck-logo.jpg ridecheck-crm-backend-1:/app/app/static/branding/ridecheck-logo.jpg` |
| Restart | `docker restart ridecheck-crm-backend-1` |

---

Outbound: OFF
Production DB touched: NO
n8n changed: NO

SAFE FOR OWNER VISUAL RECHECK: YES
