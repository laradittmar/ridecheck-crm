PROJECT: RIDECHECK_CRM
TYPE: CLOSEOUT
MILESTONE: M21.3-UX3

DATE: 2026-08-28
BRANCH: main

---

STATUS: PASS

---

AUDIT — ROUTES AND DOMAIN RELATIONSHIPS

WhatsApp Inbox route:        GET /whatsapp/inbox
Individual thread route:     GET /whatsapp/thread/{thread_id}
Debug (staff-only):          GET /integrations/whatsapp/debug

Calendar route:              GET /calendar?date=YYYY-MM-DD&highlight_lead_id=N&week=YYYY-MM-DD
Kanban (revision open):      GET /kanban?open_lead={lead_id}&open_rev={revision_id}

Domain chain (read-only, no writes):
  Revision.lead_id       → Lead.id
  WhatsAppThread.lead_id → Lead.id (join used in calendar route)
  Lead.telefono          → canonical customer phone (stored, not normalized)
  Vendedor/Agencia have own telefono fields — NOT used as customer phone

thread_by_lead built in /calendar route:
  SELECT lead_id, id FROM whatsapp_threads WHERE lead_id IN (...)
  Result: {lead_id: thread_id} — passed to render_calendar_page

---

AGENDA → WHATSAPP

Previous broken route:   /integrations/whatsapp/inbox?thread_id=<id>
HTTP status of old route: 404 Not Found (confirmed via curl)

Correct Inbox route:     /whatsapp/thread/<thread_id>
Thread resolution:       thread_by_lead dict built from WhatsAppThread.lead_id → WhatsAppThread.id
                         Lookup: wa_tid = tbl.get(lead.id)

WA navigation:           PASS
Sends message:           NO — pure <a href> navigation, no fetch/POST

When no thread:
  - WA button is not rendered (wa_tid evaluates to None/falsy)
  - No disabled state, no fake thread — element simply absent

---

AGENDA → CALL

Canonical phone source:  Lead.telefono (Lead model, column "telefono", String(40))
tel: link:               PASS
Rendered as:             <a class="agendaActionBtn agendaCallBtn" href="tel:{Lead.telefono}">📞 Llamar</a>

Missing phone behavior:
  phone = (getattr(l, "telefono", None) or "").strip()
  If empty → contact_html block not appended → no Llamar button rendered

Seller phone handling:
  Agencia.telefono exists in the domain model but is NOT rendered in the Llamar button.
  Agencia phone never touches the tel: URI. Confirmed: no tel:+54911VENDEDOR
  appears in rendered HTML when Lead.telefono differs. (UX3-08 test confirms.)

---

AGENDA → REVISION

Link:   PASS
Route:  /kanban?open_lead={lead.id}&open_rev={revision.id}
Label:  "✎ Ver revisión" (relabelled from "✎ Ver")
JS:     DOMContentLoaded opens revs-{lead_id} details + scrolls/highlights rev-{lead_id}-{rev_id}

---

REVISION → AGENDA

"Ver en agenda":       PASS
Opens correct date:    PASS — /calendar?highlight_lead_id={lead_id}&week={week}&date={turno_fecha}#day
Appointment focus:     YES — highlight_lead_id triggers JS highlightLeadCard on calendar load

Implementation:

1. Kanban revision card (render_lead_card, revs_display loop):
   When revision.turno_fecha is set, a blue pill link is appended in revHeadLine1:
     <a class="pill pill-agenda-link" href="/calendar?highlight_lead_id={l.id}&week=...&date=...#day">
       📅 Ver en agenda
     </a>

2. _render_revision_approval_ui APPROVED branch:
   When lead_id and turno_fecha are both available, "✓ Turno confirmado" becomes a link:
     <a class="pill pill-approval-approved" href="/calendar?highlight_lead_id={lead_id}&week=...&date=...#day">
       ✓ Turno confirmado → Ver en agenda
     </a>
   When turno_fecha is absent: falls back to plain span (no change from prior behavior).

---

DOMAIN

Schema migration:          NO — all navigation uses existing Revision.turno_fecha,
                           Lead.id, WhatsAppThread.lead_id already available in models
Hard-coded IDs:            NO — all IDs resolved from domain relationships at render time
UX2 regressions:           PASS — Maps, Waze, GPS dropdown, travel blocks, zero-zone all intact
UX1 regression:            PASS — /whatsapp/thread/{id} preserves full thread view with newest
                           message, thread list sidebar, and existing UX1 inbox layout

---

RUNTIME PROOF (crm_test, container ridecheck-crm-backend-1)

1. /integrations/whatsapp/inbox?thread_id=X → HTTP 404       PASS
2. /whatsapp/thread/999 (valid route, empty thread) → HTTP 200 PASS
                                                  wa-app found  PASS
3. /calendar rendered HTML:
   - /whatsapp/thread/ in HTML                                PASS
   - integrations/whatsapp/inbox?thread_id absent             PASS
   - href="tel: links present (1 occurrence)                  PASS
   - "Ver revisión" in agenda day card (2 occurrences)        PASS
   - agendaDayWrap (45 occurrences — UX2 intact)              PASS
   - google.com/maps (1 occurrence — UX2 intact)              PASS
   - waze.com (1 occurrence — UX2 intact)                     PASS
4. /kanban rendered HTML:
   - "Ver en agenda" (6 occurrences)                          PASS
   - pill-agenda-link (7 occurrences — CSS def + elements)    PASS
   - /calendar?highlight_lead_id (16 occurrences)             PASS

---

MODIFIED FILES

| File | Change |
|------|--------|
| `backend/app/ui/kanban_view.py` | WA button URL; "Ver revisión" label; Ver-en-agenda pill; pill-agenda-link CSS; APPROVED branch calendar link |

NEW FILES

| File | Purpose |
|------|---------|
| `tests/test_m21_3_ux3.py` | UX3-01–UX3-16 (16 tests) |

CONTAINER OPERATIONS

| Operation | Command |
|-----------|---------|
| Deploy kanban_view.py | `docker cp backend/app/ui/kanban_view.py ridecheck-crm-backend-1:/app/app/ui/kanban_view.py` |
| Module reload | `docker restart ridecheck-crm-backend-1` |

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
tests/test_m21_3_ux3.py                      16 passed

TOTAL: 197 passed, 0 failed, 0 skipped — 170.81s (0:02:50)
```

UX3-01  Agenda WA action points to real Inbox route: PASS
UX3-02  Agenda WA action resolves correct Thread for Lead/Revision: PASS
UX3-03  No WhatsApp Thread → action safely unavailable: PASS
UX3-04  Agenda WA navigation does NOT create/send message: PASS
UX3-05  Llamar uses canonical customer phone: PASS
UX3-06  Llamar renders tel: URI: PASS
UX3-07  missing customer phone handled safely: PASS
UX3-08  seller phone cannot silently replace customer phone: PASS
UX3-09  Agenda → Revision link opens correct Revision: PASS
UX3-10  Revision with scheduled appointment renders "Ver en agenda": PASS
UX3-11  Revision → Agenda opens correct appointment date: PASS
UX3-12  highlight_lead_id present in Revision → Agenda link: PASS
UX3-13  no hard-coded thread/lead/revision IDs: PASS
UX3-14  UX2 Maps/GPS regression passes: PASS
UX3-15  UX1 newest-message Inbox behavior preserved: PASS
UX3-16  mobile action layout no overflow: PASS

---

Agenda WA no longer 404:        PASS  (old route 404; /whatsapp/thread/ returns 200)
Inbox opens correct thread:      PASS  (navigates directly to thread view with full WA shell)
tel href rendered:               PASS  (href="tel:+..." in calendar HTML)
Revision→Agenda rendered:        PASS  ("Ver en agenda" in kanban, 6 occurrences)
Agenda→Revision rendered:        PASS  ("Ver revisión" in calendar, open_lead/open_rev links)

TESTS: 197 collected / 197 passed / 0 failed / 0 skipped

CE changed:                      NO
ScheduleService rules changed:   NO
n8n changed:                     NO
Outbound:                        OFF
Production DB touched:           NO

SAFE FOR OWNER VISUAL RECHECK:   YES
