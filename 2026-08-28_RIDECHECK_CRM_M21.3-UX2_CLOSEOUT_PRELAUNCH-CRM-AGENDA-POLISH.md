PROJECT: RIDECHECK_CRM / TYPE: CLOSEOUT / MILESTONE: M21.3-UX2
DATE: 2026-08-28
BRANCH: main

---

## Scope

M21.3-UX2 delivers pre-launch UX polish across the CRM shell and the Agenda
Day view. Changes are purely visual and routing — zero business logic,
zero outbound, zero pricing, zero scheduling rules modified.

---

## Regression Gate

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

TOTAL: 171 passed, 0 failed, 0 skipped — 171.15s (0:02:51)
```

---

## Deliverables

### 1 — Background image hot-fix (`docker cp`)

The owner replaced `backend/app/static/bg.png` (1.5 MB JPEG) before this
session. The running container had the stale build-time copy. Fix:

```bash
docker cp backend/app/static/bg.png ridecheck-crm-backend-1:/app/app/static/bg.png
```

### 2 — `backend/app/ui/kanban_view.py` (modified)

**Cache-buster for bg.png**

New module-level constant computed from the file's mtime at import time:

```python
_BG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "static", "bg.png")
try:
    _BG_VER = str(int(os.path.getmtime(_BG_PATH)))
except OSError:
    _BG_VER = "1"
```

CSS URL updated:
```css
url('/static/bg.png?v={_BG_VER}')
```

**Sidebar logo**

All 6 `<div class="brandText">RIDECHECK</div>` occurrences in
`kanban_view.py` replaced with:
```html
<img class="brandLogo" src="/static/branding/ridecheck-logo.jpg" alt="RideCheck">
```

Logo CSS added to `_base_css()`:
```css
.brandLogo { width:38px; height:38px; border-radius:8px; object-fit:cover; }
.sidebar.collapsed .brandLogo { width:34px; height:34px; }
.sidebar.collapsed .sidebarToggle svg { transform: scaleX(-1); }
```

**Sidebar footer — collapsed state fix**

Removed the hidden rule (`.sidebar.collapsed .sidebarFooter { display:none }`).
Added CSS for compact collapsed state:
```css
.sidebar.collapsed .sidebarUser { display:none; }
.sidebar.collapsed .logoutBtn { display:none; }
.sidebar.collapsed .logoutBtnCompact { display:block; }
```

`_sidebar_user_block()` updated to include a compact X-button logout that
appears only when collapsed:
```html
<button class="logoutBtnCompact" type="submit" title="Log Out">&#x2715;</button>
```

**Table view sidebar localStorage fix**

Old broken code (line ~3424):
```javascript
window.toggleSidebar = function () {
  sidebar.classList.toggle("collapsed");  // no persistence
};
```

Fixed:
```javascript
window.toggleSidebar = function () {
  var collapsed = !sidebar.classList.contains("collapsed");
  sidebar.classList.toggle("collapsed", collapsed);
  localStorage.setItem("sidebar_collapsed", collapsed ? "1" : "0");
};
window.addEventListener("DOMContentLoaded", function () {
  var sb = document.getElementById("sidebar");
  if (sb && localStorage.getItem("sidebar_collapsed") === "1")
    sb.classList.add("collapsed");
});
```

**Operational Day view (`_operational_day_view`)**

New local function inside `render_calendar_page`. Replaces the old `_day_slots`
hour-grid for the Day view panel and all 42 pre-rendered hidden divs.

Layout per day:
1. **Day header** — date, business hours string, turno count, unpaid count,
   free gap count, zero-zone start label
2. **Day-start block** — INICIO indicator, zero-zone detail, start time
3. **Travel block** (repeating) — origin → destination, required/available
   minutes, margin chip (✓ ok / ⚠ tight / ! conflict)
4. **Gap block** — shown when same-zone appointments have ≥30 min between them
5. **Appointment card** — time + end time column, customer name, vehicle,
   zone + detail, address (→ Google Maps), secondary GPS menu (Google Maps +
   Waze), estado chip, payment chip, contact actions (WA thread link, tel:)
6. **Trailing gap block** — free time at end of day if ≥30 min
7. Closed Sunday: "Domingo — sin operaciones."

**GPS dropdown JS added** (in calendar script block):
```javascript
window.toggleGpsMenu = function(btn) { … };
document.addEventListener("click", function(e) {
  if (!e.target.closest(".agendaGpsMenu"))
    document.querySelectorAll(".agendaGpsDropdown").forEach(…);
});
```

**`render_calendar_page` signature extended** (backward-compatible):
```python
def render_calendar_page(
    leads, profesionales=None, week=None, user_email="",
    highlight_lead_id=None, initial_date=None,
    schedule_svc=None,      # NEW — ScheduleService for day-start info
    thread_by_lead=None,    # NEW — {lead_id: thread_id} for WA links
) -> str:
```

When `schedule_svc=None`, falls back to a minimal default day-start dict
(no DB required — backward-compatible with all existing callers).

### 3 — `backend/app/services/schedule.py` (modified)

New public method on `ScheduleService`:

```python
def get_day_start_info(self, day: date) -> dict:
    hours = self._business_hours(day, False)
    zero_group = self._zero_zone_group(day)
    zero_detail = self._zero_zone_detail(day)
    biz_str = "cerrado" if hours.closed else self._format_hours(hours.start, hours.end)
    return {
        "is_closed": hours.closed,
        "business_hours_str": biz_str,
        "start_time": hours.start.strftime("%H:%M"),
        "end_time": hours.end.strftime("%H:%M"),
        "zero_zone_group": zero_group,
        "zero_zone_detail": zero_detail,
    }
```

Uses the same `_business_hours`, `_zero_zone_group`, `_zero_zone_detail`
methods already tested. Zero-zone detail: Mon alternates Santa Catalina /
Melo y Panamericana; Tue/Thu/Sat = Santa Catalina; Wed/Fri = Melo y Panamericana.

### 4 — `backend/app/ui/kanban.py` (modified)

Calendar route updated to supply `schedule_svc` and `thread_by_lead`:

```python
from ..services.schedule import ScheduleService

schedule_svc = ScheduleService(db)
lead_ids = [l.id for l in leads]
thread_rows = db.execute(
    select(WhatsAppThread.lead_id, WhatsAppThread.id)
    .where(WhatsAppThread.lead_id.in_(lead_ids))
).all() if lead_ids else []
thread_by_lead = {row.lead_id: row.id for row in thread_rows if row.lead_id}

render_calendar_page(
    leads, …,
    schedule_svc=schedule_svc,
    thread_by_lead=thread_by_lead,
)
```

### 5 — `backend/app/ui/whatsapp_ui.py` (modified)

Single occurrence of `<div class="brandText">RIDECHECK</div>` replaced with
the logo img (same as kanban_view.py). No functional change to any WA logic.

### 6 — `tests/test_m21_3_ux2.py` (new)

21 tests across 3 test classes:

| Group | Tests | Coverage |
|-------|-------|---------|
| `TestUX2CSS` | UX2-01 to UX2-06 | localStorage in table sidebar, calendar restore, footer not hidden, logo rendered, bg cache-buster, cover/no-repeat CSS |
| `TestUX2AgendaDay` | UX2-07 to UX2-19 | Address + locality, Google Maps link, Waze link, missing address fallback, travel route text, gap block, ok/conflict margin, zero-zone, first travel block, trailing gap, cancelled non-occupying, None fields safe |
| `TestUX2Views` | UX2-20, UX2-21 | View pills preserved, WA shell regression |

---

## Operational Day View — Business Rules

**Travel computation**: `ZoneTravelProvider.get_travel_minutes(origin, dest)`:
- Same group → 0 min (gap block rendered instead)
- CABA ↔ Norte/Oeste/Sur → 60 min
- Norte ↔ Oeste/Sur, Oeste ↔ Sur → 90 min
- None/empty → 0 min

**Margin classification**:
- `margin ≥ 15 min` → ok (✓ green)
- `0 ≤ margin < 15` → tight (⚠ amber)
- `margin < 0` → conflict (! red)

**Cancelled/REPROGRAMAR**: Cards rendered but do not contribute to travel-block
computation (their zone is skipped when tracking `prev_zone`/`prev_end_dt`).

**Free gap threshold**: ≥ 30 min → gap block shown (between appointments or
at end of day).

**Address→Maps**: `https://www.google.com/maps/search/?api=1&query=<addr>+<zone>`
**Waze link**: `https://waze.com/ul?q=<addr>`
No API key required for either.

**WA thread link**: `/integrations/whatsapp/inbox?thread_id=<id>`
Only shown when a matching WhatsAppThread.lead_id exists in `thread_by_lead`.

---

## Security Invariants (Unchanged)

- **OUTBOUND REMAINS OFF** — no WhatsApp messages created by any UX2 code
- **Production untouched** — all work on release-candidate
- **n8n unmodified**
- **ConversationEngine unmodified**
- **Pricing / scheduling rules unmodified**
- **Meta token / webhook security unmodified**
- **Booking Flow**: NOT published, NOT connected, private key unchanged

---

## Modified Files

| File | Change |
|------|--------|
| `backend/app/ui/kanban_view.py` | Logo, collapsed footer, localStorage, bg cache-buster, operational day view |
| `backend/app/ui/whatsapp_ui.py` | Logo img (replaces brandText) |
| `backend/app/ui/kanban.py` | Calendar route: ScheduleService + thread_by_lead |
| `backend/app/services/schedule.py` | `get_day_start_info()` method |

## New Files

| File | Purpose |
|------|---------|
| `tests/test_m21_3_ux2.py` | UX2-01–UX2-21 test suite (21 tests) |

## Container Operations

| Operation | Command |
|-----------|---------|
| Hot-copy bg.png | `docker cp backend/app/static/bg.png ridecheck-crm-backend-1:/app/app/static/bg.png` |
| Reload changes | Container reads static files live; Python modules require restart |
| Restart backend | `docker compose restart backend` |
