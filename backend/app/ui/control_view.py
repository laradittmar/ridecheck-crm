# app/ui/control_view.py
"""
Control dashboard page for the RideCheck CRM.
Renders the operational monitoring dashboard as a complete HTML string.
All data panels are client-fetched via JavaScript fetch() from API endpoints.
"""
from __future__ import annotations

import html as html_lib
import os as _os

from .components import (
    render_sidebar_nav,
    render_sidebar_ai_block,
    render_sidebar_ai_script,
    render_whatsapp_icon_svg,
)
from .kanban_view import _sidebar_user_block, _BG_VER

# ---------------------------------------------------------------------------
# Constants / icons
# ---------------------------------------------------------------------------

ICON_CONTROL = (
    '<svg class="icon icon-only" viewBox="0 0 24 24" width="18" height="18" '
    'fill="none" stroke="currentColor" stroke-width="1.8" '
    'stroke-linecap="round" stroke-linejoin="round">'
    '<rect x="2" y="3" width="20" height="14" rx="2"/>'
    '<path d="M8 21h8"/>'
    '<path d="M12 17v4"/>'
    '</svg>'
)

ICON_HAMBURGER = (
    '<svg class="icon icon-only" viewBox="0 0 24 24" width="18" height="18" '
    'fill="none" stroke="currentColor" stroke-width="1.8" '
    'stroke-linecap="round" stroke-linejoin="round">'
    '<path d="M4 7h16"/><path d="M4 12h16"/><path d="M4 17h16"/>'
    '</svg>'
)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _control_sidebar_nav() -> str:
    """Build sidebar nav HTML that includes the Control link."""
    icon_board = (
        '<svg class="icon icon-only" viewBox="0 0 24 24" width="18" height="18" '
        'fill="none" stroke="currentColor" stroke-width="1.8" '
        'stroke-linecap="round" stroke-linejoin="round">'
        '<rect x="3" y="3" width="7" height="7" rx="1"/>'
        '<rect x="14" y="3" width="7" height="7" rx="1"/>'
        '<rect x="3" y="14" width="7" height="7" rx="1"/>'
        '<rect x="14" y="14" width="7" height="7" rx="1"/>'
        '</svg>'
    )
    icon_calendar = (
        '<svg class="icon icon-only" viewBox="0 0 24 24" width="18" height="18" '
        'fill="none" stroke="currentColor" stroke-width="1.8" '
        'stroke-linecap="round" stroke-linejoin="round">'
        '<rect x="3" y="4" width="18" height="17" rx="2"/>'
        '<path d="M16 2v4"/><path d="M8 2v4"/><path d="M3 10h18"/>'
        '</svg>'
    )
    icon_filter = (
        '<svg class="icon icon-only" viewBox="0 0 24 24" width="18" height="18" '
        'fill="none" stroke="currentColor" stroke-width="1.8" '
        'stroke-linecap="round" stroke-linejoin="round">'
        '<path d="M4 6h16M7 12h10M10 18h4"/>'
        '</svg>'
    )
    icon_prof = (
        '<svg class="icon icon-only" viewBox="0 0 24 24" width="18" height="18" '
        'fill="none" stroke="currentColor" stroke-width="1.8" '
        'stroke-linecap="round" stroke-linejoin="round">'
        '<circle cx="12" cy="8" r="4"/>'
        '<path d="M4 20c0-4 3.6-7 8-7s8 3 8 7"/>'
        '</svg>'
    )
    icon_ag = (
        '<svg class="icon icon-only" viewBox="0 0 24 24" width="18" height="18" '
        'fill="none" stroke="currentColor" stroke-width="1.8" '
        'stroke-linecap="round" stroke-linejoin="round">'
        '<path d="M3 21h18M3 7l9-4 9 4M4 7v14M20 7v14M9 21v-4h6v4"/>'
        '</svg>'
    )
    icon_wa = render_whatsapp_icon_svg()

    items = [
        ("/kanban",         "CRM",             icon_board,    ""),
        ("/calendar",       "Calendario",      icon_calendar, ""),
        ("/table",          "Filtros",         icon_filter,   ""),
        ("/profesionales",  "Profesionales",   icon_prof,     ""),
        ("/agencias",       "Agencias",        icon_ag,       ""),
        ("/whatsapp/inbox", "WhatsApp Inbox",  icon_wa,       " waNavIcon"),
        ("/control",        "Control",         ICON_CONTROL,  ""),
    ]
    links = "".join(
        f'<a href="{html_lib.escape(href, quote=True)}">'
        f'<span class="navIcon{extra}">{icon}</span>'
        f'<span class="navLabel">{html_lib.escape(label)}</span>'
        f'</a>'
        for href, label, icon, extra in items
    )
    active_script = (
        "<script>(function(){"
        "var seg='/'+location.pathname.split('/')[1];"
        "document.querySelectorAll('.nav a').forEach(function(a){"
        "var h=(a.getAttribute('href')||'').split('?')[0];"
        "if(seg.length>1&&h.startsWith(seg))a.classList.add('active');"
        "});})();</script>"
    )
    return f'<div class="nav">{links}</div>{active_script}'


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def render_control_page(user_email: str) -> str:
    """Return a complete HTML string for the Control operational dashboard."""
    sidebar_nav = _control_sidebar_nav()
    user_block = _sidebar_user_block(user_email)

    return f"""<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Control — Ridecheck CRM</title>
  <style>
    /* ---- CSS variables / reset ---- */
    :root {{
      --bg: #f3f4f6;
      --card: #ffffff;
      --muted: #6b7280;
      --border: #e5e7eb;
      --radius: clamp(8px, 0.42vw, 12px);
      --gap: clamp(6px, 0.38vw, 10px);
      --shadow: 0 2px 8px rgba(0,0,0,.08);
      --shadow2: 0 6px 18px rgba(0,0,0,.08);
      --font-base: clamp(12px, 0.72vw, 14px);
      --font-sm: clamp(10px, 0.58vw, 12px);

      /* status colours */
      --clr-normal: #22c55e;
      --clr-warning: #f59e0b;
      --clr-critical: #ef4444;
      --clr-off: #6b7280;       /* outbound OFF — calm grey; OFF is the expected dev state */
      --clr-on: #22c55e;        /* outbound ON */
      --clr-blocker: #ef4444;
      --clr-human: #a855f7;
      --clr-unanswered: #f59e0b;
      --clr-waiting: #3b82f6;
    }}

    *, *::before, *::after {{ box-sizing: border-box; }}
    body {{
      font-family: Arial, sans-serif;
      margin: 0;
      background: var(--bg);
      font-size: var(--font-base);
      color: #111827;
    }}
    a {{ color: #2563eb; text-decoration: underline; }}

    /* ---- Layout ---- */
    .layout {{
      display: flex;
      min-height: 100vh;
      position: relative;
      isolation: isolate;
    }}
    .layout::before {{
      content: "";
      position: fixed;
      inset: 0;
      background-image:
        linear-gradient(180deg, rgba(255,255,255,.1), rgba(243,244,246,.15)),
        url('/static/bg.png?v={_BG_VER}');
      background-size: cover;
      background-position: center;
      background-repeat: no-repeat;
      pointer-events: none;
      z-index: -1;
    }}

    /* ---- Sidebar ---- */
    .sidebar {{
      width: 232px;
      background: #111827;
      color: #fff;
      padding: 12px;
      position: sticky;
      top: 0;
      height: 100vh;
      transition: width .15s ease;
      display: flex;
      flex-direction: column;
      flex-shrink: 0;
    }}
    .sidebar.collapsed {{ width: 68px; }}
    .brandRow {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 8px;
      margin-bottom: 12px;
    }}
    .brandLogo {{
      width: 38px; height: 38px;
      border-radius: 8px;
      object-fit: cover;
      transition: width .15s, height .15s;
    }}
    .sidebar.collapsed .brandLogo {{ width: 34px; height: 34px; }}
    .sidebarToggle {{
      border: none;
      background: transparent;
      color: #e5e7eb;
      cursor: pointer;
      padding: 4px;
      border-radius: 8px;
    }}
    .sidebarToggle:hover {{ background: rgba(255,255,255,.08); }}
    .sidebar.collapsed .sidebarToggle svg {{ transform: scaleX(-1); }}

    /* ---- Nav ---- */
    .nav {{
      padding-bottom: 12px;
      border-bottom: 1px solid rgba(255,255,255,.16);
    }}
    .nav a {{
      display: flex;
      align-items: center;
      gap: 10px;
      padding: 10px;
      border-radius: 10px;
      color: #e5e7eb;
      text-decoration: none;
      margin-bottom: 8px;
    }}
    .nav a:hover {{ background: rgba(255,255,255,.08); }}
    .nav a.active {{ color: #fff; }}
    .nav a.active .icon,
    .nav a.active svg {{ filter: brightness(0) invert(1); }}
    .navIcon {{ display: inline-flex; color: #fff; }}
    .waNavIcon {{
      display: flex;
      align-items: center;
      justify-content: center;
      line-height: 0;
    }}
    .navIcon svg {{ width: 18px; height: 18px; display: block; }}
    .waNavIcon svg {{
      display: block;
      width: 18px; height: 18px;
      overflow: visible;
      stroke: currentColor;
      fill: none;
      stroke-width: 2;
      shape-rendering: geometricPrecision;
      vector-effect: non-scaling-stroke;
    }}
    .navLabel {{ white-space: nowrap; }}
    .sidebar.collapsed .navLabel {{ display: none; }}
    .sidebar.collapsed .nav a {{ justify-content: center; }}

    /* ---- Sidebar footer ---- */
    .sidebarFooter {{
      margin-top: auto;
      padding-top: 12px;
      padding-bottom: 18px;
      border-top: 1px solid rgba(255,255,255,.16);
    }}
    .sidebarAiBlock {{
      display: flex;
      flex-direction: column;
      gap: 8px;
      margin-bottom: 14px;
    }}
    .sidebarAiTitle {{
      font-size: 12px;
      font-weight: 700;
      letter-spacing: .2px;
      color: #f9fafb;
    }}
    .sidebarAiSubtitle {{
      font-size: 11px;
      line-height: 1.35;
      color: rgba(255,255,255,.8);
    }}
    .sidebarAiToggle {{
      position: relative;
      width: 54px;
      height: 30px;
      border: none;
      border-radius: 999px;
      background: #ef4444;
      color: #fff;
      cursor: pointer;
      transition: background .18s ease, opacity .18s ease, box-shadow .18s ease;
      box-shadow: inset 0 0 0 1px rgba(255,255,255,.12);
    }}
    .sidebarAiToggle:hover:not(:disabled) {{
      box-shadow: inset 0 0 0 1px rgba(255,255,255,.18), 0 4px 10px rgba(0,0,0,.18);
    }}
    .sidebarAiToggle:focus-visible {{
      outline: 2px solid rgba(255,255,255,.85);
      outline-offset: 2px;
    }}
    .sidebarAiToggle:disabled {{ cursor: wait; }}
    .sidebarAiToggle.is-on {{ background: #22c55e; }}
    .sidebarAiToggle.is-off {{ background: #ef4444; }}
    .sidebarAiToggle.is-loading {{ opacity: .7; }}
    .sidebarAiGlyph {{
      position: absolute;
      top: 50%;
      transform: translateY(-50%);
      font-size: 12px;
      font-weight: 700;
      line-height: 1;
      opacity: .7;
      pointer-events: none;
    }}
    .sidebarAiGlyphOff {{ right: 10px; }}
    .sidebarAiGlyphOn  {{ left: 10px; }}
    .sidebarAiToggle.is-on  .sidebarAiGlyphOff {{ opacity: 0; }}
    .sidebarAiToggle.is-off .sidebarAiGlyphOn  {{ opacity: 0; }}
    .sidebarAiKnob {{
      position: absolute;
      top: 3px; left: 3px;
      width: 24px; height: 24px;
      border-radius: 50%;
      background: #fff;
      box-shadow: 0 2px 8px rgba(15,23,42,.28);
      transition: transform .18s ease;
    }}
    .sidebarAiToggle.is-on .sidebarAiKnob {{ transform: translateX(24px); }}
    .sidebarAiStatus {{
      min-height: 14px;
      font-size: 10px;
      color: rgba(255,255,255,.72);
    }}
    .sidebarAiStatus.is-error {{ color: #fca5a5; }}
    .sidebarUser {{
      font-size: 12px;
      color: #d1d5db;
      margin-bottom: 8px;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }}
    .logoutBtn {{
      width: 100%;
      border: 1px solid rgba(255,255,255,.24);
      background: transparent;
      color: #f9fafb;
      border-radius: 8px;
      padding: 7px 8px;
      cursor: pointer;
    }}
    .logoutBtn:hover {{ background: rgba(255,255,255,.1); }}
    .logoutBtnCompact {{
      display: none;
      width: 100%;
      border: 1px solid rgba(255,255,255,.24);
      background: transparent;
      color: #f9fafb;
      border-radius: 8px;
      padding: 7px 4px;
      cursor: pointer;
      font-size: 13px;
    }}
    .logoutBtnCompact:hover {{ background: rgba(255,255,255,.1); }}
    .sidebar.collapsed .sidebarAiBlock {{ display: none; }}
    .sidebar.collapsed .sidebarUser {{ display: none; }}
    .sidebar.collapsed .logoutBtn {{ display: none; }}
    .sidebar.collapsed .logoutBtnCompact {{ display: block; }}

    /* ---- Main control area ---- */
    .controlMain {{
      flex: 1;
      padding: clamp(16px, 1.2vw, 28px);
      min-width: 0;
      position: relative;
      z-index: 1;
    }}

    /* ---- Control header ---- */
    .controlHeader {{
      display: flex;
      align-items: center;
      gap: 14px;
      flex-wrap: wrap;
      background: #fff;
      border: 1px solid var(--border);
      border-radius: var(--radius);
      box-shadow: var(--shadow);
      padding: clamp(10px, 0.6vw, 14px) clamp(12px, 0.8vw, 18px);
      margin-bottom: 16px;
      position: sticky;
      top: 0;
      z-index: 30;
    }}
    .controlHeader h1 {{
      margin: 0;
      font-size: clamp(16px, 1vw, 22px);
      font-weight: 700;
      color: #111827;
      flex: 0 0 auto;
    }}
    .refreshInfo {{
      font-size: var(--font-sm);
      color: var(--muted);
      flex: 1 1 auto;
    }}
    .refreshInfo span {{ font-weight: 600; color: #374151; }}
    .headerRight {{
      display: flex;
      align-items: center;
      gap: 8px;
      flex-wrap: wrap;
    }}
    .windowControls {{
      display: flex;
      gap: 4px;
    }}
    .windowBtn {{
      border: 1px solid var(--border);
      background: #f9fafb;
      color: #374151;
      border-radius: 6px;
      padding: 5px 12px;
      font-size: var(--font-sm);
      cursor: pointer;
      font-weight: 600;
      transition: background .12s, border-color .12s;
    }}
    .windowBtn:hover {{ background: #f3f4f6; border-color: #d1d5db; }}
    .windowBtn.active {{
      background: #1d4ed8;
      border-color: #1d4ed8;
      color: #fff;
    }}
    .pauseBtn {{
      border: 1px solid var(--border);
      background: #f9fafb;
      color: #374151;
      border-radius: 6px;
      padding: 5px 12px;
      font-size: var(--font-sm);
      cursor: pointer;
      font-weight: 600;
    }}
    .pauseBtn.paused {{
      background: #fef3c7;
      border-color: #fcd34d;
      color: #92400e;
    }}
    .pauseBtn:hover {{ background: #f3f4f6; }}

    /* ---- Status bar cards ---- */
    .statusBar {{
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      margin-bottom: 16px;
    }}
    .statusCard {{
      background: #fff;
      border: 1px solid var(--border);
      border-radius: var(--radius);
      box-shadow: var(--shadow);
      padding: 12px 16px;
      min-width: 130px;
      flex: 1 1 130px;
      max-width: 200px;
    }}
    .cardLabel {{
      font-size: 10px;
      font-weight: 700;
      letter-spacing: .6px;
      text-transform: uppercase;
      color: var(--muted);
      margin-bottom: 4px;
    }}
    .cardValue {{
      font-size: clamp(18px, 1.4vw, 26px);
      font-weight: 700;
      color: #111827;
    }}
    /* Outbound OFF is calm grey-blue — OFF is normal/expected during development */
    .outboundOff {{
      color: #6b7280;
      background: #f1f5f9;
      border-radius: 6px;
      padding: 2px 10px;
      font-size: clamp(14px, 1vw, 18px);
    }}
    .outboundOn {{
      color: var(--clr-on);
      background: #dcfce7;
      border-radius: 6px;
      padding: 2px 10px;
      font-size: clamp(14px, 1vw, 18px);
    }}
    .threshNormal  {{ color: var(--clr-normal); }}
    .threshWarning {{ color: var(--clr-warning); }}
    .threshCritical {{ color: var(--clr-critical); }}

    /* ---- Panels ---- */
    .panel {{
      background: #fff;
      border: 1px solid var(--border);
      border-radius: var(--radius);
      box-shadow: var(--shadow);
      margin-bottom: 16px;
      overflow: hidden;
    }}
    .panelHeader {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      flex-wrap: wrap;
      gap: 8px;
      padding: 12px 16px 10px;
      border-bottom: 1px solid var(--border);
    }}
    .panelHeader h2 {{
      margin: 0;
      font-size: clamp(13px, 0.8vw, 16px);
      font-weight: 700;
      color: #111827;
    }}
    .filterGroup {{
      display: flex;
      gap: 4px;
      flex-wrap: wrap;
    }}
    .filterBtn {{
      border: 1px solid var(--border);
      background: #f9fafb;
      color: #374151;
      border-radius: 999px;
      padding: 3px 10px;
      font-size: var(--font-sm);
      cursor: pointer;
      font-weight: 600;
      white-space: nowrap;
    }}
    .filterBtn:hover {{ background: #f3f4f6; }}
    .filterBtn.active {{
      background: #1d4ed8;
      border-color: #1d4ed8;
      color: #fff;
    }}

    /* ---- Tables ---- */
    .tableWrap {{
      overflow-x: auto;
      -webkit-overflow-scrolling: touch;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      font-size: var(--font-sm);
    }}
    thead th {{
      background: #f9fafb;
      color: var(--muted);
      font-weight: 700;
      text-align: left;
      padding: 8px 12px;
      border-bottom: 1px solid var(--border);
      white-space: nowrap;
      font-size: 10px;
      letter-spacing: .4px;
      text-transform: uppercase;
    }}
    tbody tr {{
      border-bottom: 1px solid #f3f4f6;
      transition: background .1s;
    }}
    tbody tr:hover {{ background: #f9fafb; }}
    tbody td {{
      padding: 8px 12px;
      color: #374151;
      vertical-align: top;
    }}
    .emptyState {{
      text-align: center;
      color: var(--muted);
      padding: 28px 12px;
      font-size: var(--font-sm);
    }}

    /* ---- Row health colours ---- */
    .rowCritical td:first-child {{ border-left: 3px solid var(--clr-critical); }}
    .rowCritical {{ background: #fff1f2; }}
    .rowCritical:hover {{ background: #ffe4e6; }}
    .rowUnanswered {{ background: #fffbeb; }}
    .rowUnanswered:hover {{ background: #fef3c7; }}
    .rowUnanswered td:first-child {{ border-left: 3px solid var(--clr-unanswered); }}
    .rowHuman {{ background: #faf5ff; }}
    .rowHuman:hover {{ background: #f3e8ff; }}
    .rowHuman td:first-child {{ border-left: 3px solid var(--clr-human); }}
    .rowWaiting {{ background: #eff6ff; }}
    .rowWaiting:hover {{ background: #dbeafe; }}
    .rowWaiting td:first-child {{ border-left: 3px solid var(--clr-waiting); }}

    /* ---- Severity / path badges ---- */
    .badge {{
      display: inline-block;
      padding: 2px 8px;
      border-radius: 999px;
      font-size: 10px;
      font-weight: 700;
      letter-spacing: .3px;
      text-transform: uppercase;
      white-space: nowrap;
    }}
    .badgeBlocker  {{ background: #fee2e2; color: #991b1b; border: 1px solid #fca5a5; }}
    .badgeCritical {{ background: #fee2e2; color: #991b1b; border: 1px solid #fca5a5; }}
    .badgeHigh     {{ background: #fef3c7; color: #92400e; border: 1px solid #fcd34d; }}
    .badgeMedium   {{ background: #eff6ff; color: #1e40af; border: 1px solid #bfdbfe; }}
    .badgeLow      {{ background: #f0fdf4; color: #166534; border: 1px solid #86efac; }}
    .badgeUnknown  {{ background: #fef2f2; color: #991b1b; border: 1px solid #fca5a5; }}
    .badgeOk       {{ background: #f0fdf4; color: #166534; border: 1px solid #86efac; }}
    .badgeOff      {{
      /* calm grey-blue — not alarming, OFF is normal for development */
      background: #f1f5f9; color: #475569; border: 1px solid #cbd5e1;
    }}
    .dirIn  {{ background: #dcfce7; color: #166534; border: 1px solid #86efac; }}
    .dirOut {{ background: #eff6ff; color: #1e40af; border: 1px solid #bfdbfe; }}

    /* ---- Message detail expand row ---- */
    .detailRow {{ display: none; }}
    .detailRow.open {{ display: table-row; }}
    .detailRow td {{
      background: #f9fafb;
      padding: 10px 16px;
      font-size: 11px;
      color: #374151;
    }}
    .detailGrid {{
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
      gap: 8px;
    }}
    .detailItem {{ display: flex; flex-direction: column; gap: 2px; }}
    .detailKey {{
      font-size: 10px;
      text-transform: uppercase;
      letter-spacing: .4px;
      color: var(--muted);
      font-weight: 700;
    }}
    .detailVal {{
      font-family: monospace;
      font-size: 11px;
      color: #111827;
      word-break: break-all;
    }}
    .expandCursor {{ cursor: pointer; }}

    /* ---- Critical events panel ---- */
    .criticalPanel .panelHeader {{ border-left: 3px solid var(--clr-critical); }}
    .eventItem {{
      display: flex;
      align-items: flex-start;
      gap: 10px;
      padding: 10px 16px;
      border-bottom: 1px solid #f3f4f6;
    }}
    .eventItem:last-child {{ border-bottom: none; }}
    .eventTime {{
      font-size: 10px;
      color: var(--muted);
      white-space: nowrap;
      padding-top: 2px;
      min-width: 60px;
    }}
    .eventBody {{ flex: 1; min-width: 0; }}
    .eventType {{
      font-size: 11px;
      font-weight: 700;
      color: #111827;
      margin-bottom: 2px;
    }}
    .eventDesc {{
      font-size: 11px;
      color: #374151;
      word-break: break-word;
    }}
    .eventMeta {{
      font-size: 10px;
      color: var(--muted);
      margin-top: 3px;
    }}
    .criticalEmpty {{
      padding: 24px 16px;
      color: var(--muted);
      font-size: var(--font-sm);
      text-align: center;
    }}

    /* ---- Latency display ---- */
    .latencyWrap {{ display: flex; gap: 6px; align-items: baseline; }}
    .latencyLabel {{
      font-size: 10px;
      color: var(--muted);
      text-transform: uppercase;
      letter-spacing: .4px;
    }}
    .latencyVal {{ font-weight: 700; }}

    /* ---- Mobile ---- */
    @media (max-width: 768px) {{
      .sidebar {{
        position: fixed;
        top: 0; left: 0;
        height: 100vh;
        z-index: 100;
        transform: translateX(-100%);
        transition: transform .2s ease, width .15s ease;
      }}
      .sidebar.mobileOpen {{ transform: translateX(0); }}
      .controlMain {{ padding: 12px; }}
      .statusCard {{ min-width: 110px; max-width: 50%; }}
      .controlHeader {{ position: relative; }}
      .panelHeader {{ flex-direction: column; align-items: flex-start; }}
      table {{ font-size: 11px; }}
      thead th, tbody td {{ padding: 6px 8px; }}
    }}
    @media (max-width: 480px) {{
      .statusCard {{ max-width: 100%; }}
      .controlHeader h1 {{ font-size: 16px; }}
    }}
  </style>
</head>
<body>
  <div class="layout">

    <!-- Sidebar -->
    <aside class="sidebar" id="sidebar">
      <div class="brandRow">
        <img class="brandLogo" src="/static/branding/ridecheck-logo.jpg" alt="RideCheck">
        <button class="sidebarToggle" type="button" onclick="toggleSidebar()" title="Colapsar barra lateral">
          {ICON_HAMBURGER}
        </button>
      </div>

      {sidebar_nav}

      {user_block}
    </aside>

    <!-- Main content -->
    <main class="controlMain">

      <!-- Header -->
      <div class="controlHeader">
        <h1>Control</h1>
        <div class="refreshInfo">
          Actualizado: <span id="lastUpdated">—</span>
        </div>
        <div class="headerRight">
          <div class="windowControls">
            <button class="windowBtn active" data-window="today" onclick="setWindow(this)">Hoy</button>
            <button class="windowBtn" data-window="24h" onclick="setWindow(this)">24h</button>
            <button class="windowBtn" data-window="7d" onclick="setWindow(this)">7d</button>
          </div>
          <button class="pauseBtn" id="pauseBtn" onclick="togglePause()">Pausar</button>
        </div>
      </div>

      <!-- Status cards -->
      <div class="statusBar" id="statusBar">
        <div class="statusCard">
          <div class="cardLabel">OUTBOUND</div>
          <div class="cardValue" id="outbound-state">—</div>
        </div>
        <div class="statusCard">
          <div class="cardLabel">INBOUND HOY</div>
          <div class="cardValue" id="stat-inbound">—</div>
        </div>
        <div class="statusCard">
          <div class="cardLabel">OUTBOUND HOY</div>
          <div class="cardValue" id="stat-outbound-count">—</div>
        </div>
        <div class="statusCard">
          <div class="cardLabel">SIN RESPUESTA</div>
          <div class="cardValue" id="stat-unanswered">—</div>
        </div>
        <div class="statusCard">
          <div class="cardLabel">NECESITA HUMANO</div>
          <div class="cardValue" id="stat-needs-human">—</div>
        </div>
        <div class="statusCard">
          <div class="cardLabel">CRÍTICOS</div>
          <div class="cardValue" id="stat-critical">—</div>
        </div>
        <div class="statusCard">
          <div class="cardLabel">LATENCIA P50</div>
          <div class="cardValue" id="stat-p50">—</div>
        </div>
        <div class="statusCard">
          <div class="cardLabel">LATENCIA P95</div>
          <div class="cardValue" id="stat-p95">—</div>
        </div>
      </div>

      <!-- Conversations panel -->
      <div class="panel" id="panel-threads">
        <div class="panelHeader">
          <h2>Conversaciones</h2>
          <div class="filterGroup" id="healthFilters">
            <button class="filterBtn active" data-health="all" onclick="setHealth(this)">Todas</button>
            <button class="filterBtn" data-health="unanswered" onclick="setHealth(this)">Sin respuesta</button>
            <button class="filterBtn" data-health="needs_human" onclick="setHealth(this)">Humano</button>
            <button class="filterBtn" data-health="waiting_customer" onclick="setHealth(this)">Esperando cliente</button>
          </div>
        </div>
        <div class="tableWrap">
          <table id="threads-table">
            <thead>
              <tr>
                <th>Cliente</th>
                <th>Última actividad</th>
                <th>Dirección</th>
                <th>Etapa</th>
                <th>Humano</th>
                <th>Esperando</th>
                <th>Antigüedad</th>
                <th>Estado</th>
                <th>Ver</th>
              </tr>
            </thead>
            <tbody id="threads-tbody">
              <tr><td colspan="9" class="emptyState">Cargando…</td></tr>
            </tbody>
          </table>
        </div>
      </div>

      <!-- Message trace panel -->
      <div class="panel" id="panel-msgs">
        <div class="panelHeader">
          <h2>Trazado de mensajes</h2>
          <div class="filterGroup" id="dirFilters">
            <button class="filterBtn active" data-dir="" onclick="setDir(this)">Todos</button>
            <button class="filterBtn" data-dir="in" onclick="setDir(this)">IN</button>
            <button class="filterBtn" data-dir="out" onclick="setDir(this)">OUT</button>
          </div>
        </div>
        <div class="tableWrap">
          <table id="msgs-table">
            <thead>
              <tr>
                <th>Hora</th>
                <th>Dir</th>
                <th>Cliente</th>
                <th>Tipo</th>
                <th>Vista previa</th>
                <th>Camino</th>
                <th>Estado</th>
              </tr>
            </thead>
            <tbody id="msgs-tbody">
              <tr><td colspan="7" class="emptyState">Cargando…</td></tr>
            </tbody>
          </table>
        </div>
      </div>

      <!-- Critical events panel -->
      <div class="panel criticalPanel" id="panel-critical">
        <div class="panelHeader">
          <h2>Eventos Críticos</h2>
        </div>
        <div id="critical-list"><div class="criticalEmpty">Cargando…</div></div>
      </div>

      <!-- Path monitoring panel -->
      <div class="panel" id="panel-paths">
        <div class="panelHeader">
          <h2>Caminos de Envío</h2>
        </div>
        <div class="tableWrap">
          <table id="paths-table">
            <thead>
              <tr>
                <th>Camino</th>
                <th>Total</th>
                <th>Exitosos</th>
                <th>Bloqueados</th>
                <th>Fallidos</th>
                <th>Estado</th>
              </tr>
            </thead>
            <tbody id="paths-tbody">
              <tr><td colspan="6" class="emptyState">Cargando…</td></tr>
            </tbody>
          </table>
        </div>
      </div>

    </main>
  </div>

  <script>
    // -------------------------------------------------------------------------
    // Utility functions
    // -------------------------------------------------------------------------
    function esc(s) {{
      if (s === null || s === undefined) return '—';
      return String(s)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
    }}

    function fmtAge(seconds) {{
      if (seconds === null || seconds === undefined || isNaN(seconds)) return '—';
      seconds = Math.floor(Number(seconds));
      if (seconds < 60) return seconds + 's';
      if (seconds < 3600) return Math.floor(seconds / 60) + 'm ' + (seconds % 60) + 's';
      return Math.floor(seconds / 3600) + 'h ' + Math.floor((seconds % 3600) / 60) + 'm';
    }}

    function fmtLatency(ms) {{
      if (ms === null || ms === undefined || ms === '') return '—';
      return (Number(ms) / 1000).toFixed(1) + 's';
    }}

    function fmtTime(iso) {{
      if (!iso) return '—';
      try {{
        var d = new Date(iso);
        return d.toLocaleTimeString('es-AR', {{ hour: '2-digit', minute: '2-digit', second: '2-digit' }});
      }} catch(e) {{ return esc(iso); }}
    }}

    function fmtDateTime(iso) {{
      if (!iso) return '—';
      try {{
        var d = new Date(iso);
        return d.toLocaleDateString('es-AR', {{ day:'2-digit', month:'2-digit' }}) + ' ' +
               d.toLocaleTimeString('es-AR', {{ hour:'2-digit', minute:'2-digit' }});
      }} catch(e) {{ return esc(iso); }}
    }}

    function nowHMS() {{
      return new Date().toLocaleTimeString('es-AR', {{ hour:'2-digit', minute:'2-digit', second:'2-digit' }});
    }}

    function severityBadgeClass(sev) {{
      if (!sev) return 'badgeMedium';
      var s = String(sev).toUpperCase();
      if (s === 'BLOCKER') return 'badgeBlocker';
      if (s === 'CRITICAL') return 'badgeCritical';
      if (s === 'HIGH')     return 'badgeHigh';
      if (s === 'MEDIUM')   return 'badgeMedium';
      if (s === 'LOW')      return 'badgeLow';
      return 'badgeMedium';
    }}

    function dirBadge(dir) {{
      if (!dir) return '—';
      var d = String(dir).toLowerCase();
      if (d === 'in')  return '<span class="badge dirIn">IN</span>';
      if (d === 'out') return '<span class="badge dirOut">OUT</span>';
      return esc(dir);
    }}

    function healthClass(health) {{
      if (!health) return '';
      var h = String(health).toLowerCase();
      if (h === 'critical' || h === 'blocker') return 'rowCritical';
      if (h === 'unanswered' || h === 'unanswered_bot') return 'rowUnanswered';
      if (h === 'needs_human' || h === 'waiting_human') return 'rowHuman';
      if (h === 'waiting_customer') return 'rowWaiting';
      return '';
    }}

    function pathStatusBadge(status, pathId) {{
      if (!pathId || String(pathId).toLowerCase() === 'unknown' ||
          String(pathId).toLowerCase() === 'legacy') {{
        return '<span class="badge badgeUnknown">CRÍTICO</span>';
      }}
      if (!status) return '<span class="badge badgeMedium">—</span>';
      var s = String(status).toUpperCase();
      if (s === 'OK' || s === 'NORMAL') return '<span class="badge badgeOk">OK</span>';
      if (s === 'WARNING')  return '<span class="badge badgeHigh">ALERTA</span>';
      if (s === 'CRITICAL') return '<span class="badge badgeCritical">CRÍTICO</span>';
      return '<span class="badge badgeMedium">' + esc(status) + '</span>';
    }}

    function threshClass(value, warn, crit) {{
      if (value === null || value === undefined) return '';
      if (crit !== undefined && value >= crit) return 'threshCritical';
      if (warn !== undefined && value >= warn)  return 'threshWarning';
      return 'threshNormal';
    }}

    // -------------------------------------------------------------------------
    // State
    // -------------------------------------------------------------------------
    var currentWindow = 'today';
    var currentHealth = 'all';
    var currentDir    = '';
    var refreshInterval = null;
    var REFRESH_MS = 10000;
    var paused = false;

    // -------------------------------------------------------------------------
    // Window / filter pickers
    // -------------------------------------------------------------------------
    function setWindow(btn) {{
      document.querySelectorAll('.windowBtn').forEach(function(b) {{ b.classList.remove('active'); }});
      btn.classList.add('active');
      currentWindow = btn.getAttribute('data-window');
      refreshAll();
    }}

    function setHealth(btn) {{
      document.querySelectorAll('#healthFilters .filterBtn').forEach(function(b) {{ b.classList.remove('active'); }});
      btn.classList.add('active');
      currentHealth = btn.getAttribute('data-health');
      fetchThreads();
    }}

    function setDir(btn) {{
      document.querySelectorAll('#dirFilters .filterBtn').forEach(function(b) {{ b.classList.remove('active'); }});
      btn.classList.add('active');
      currentDir = btn.getAttribute('data-dir');
      fetchMessages();
    }}

    // -------------------------------------------------------------------------
    // Pause / resume
    // -------------------------------------------------------------------------
    function togglePause() {{
      paused = !paused;
      var btn = document.getElementById('pauseBtn');
      if (paused) {{
        btn.textContent = 'Reanudar';
        btn.classList.add('paused');
        if (refreshInterval) {{ clearInterval(refreshInterval); refreshInterval = null; }}
      }} else {{
        btn.textContent = 'Pausar';
        btn.classList.remove('paused');
        startAutoRefresh();
        refreshAll();
      }}
    }}

    function startAutoRefresh() {{
      if (refreshInterval) clearInterval(refreshInterval);
      refreshInterval = setInterval(function() {{
        if (!paused) refreshAll();
      }}, REFRESH_MS);
    }}

    // -------------------------------------------------------------------------
    // Fetch helpers
    // -------------------------------------------------------------------------
    function apiFetch(url, cb) {{
      fetch(url, {{ credentials: 'same-origin', headers: {{ 'Accept': 'application/json' }} }})
        .then(function(r) {{
          if (!r.ok) throw new Error('HTTP ' + r.status);
          return r.json();
        }})
        .then(cb)
        .catch(function(err) {{
          console.warn('[Control] fetch failed:', url, err);
        }});
    }}

    // -------------------------------------------------------------------------
    // Summary / status cards
    // -------------------------------------------------------------------------
    function fetchSummary() {{
      apiFetch('/api/ops/summary?window=' + encodeURIComponent(currentWindow), function(data) {{
        // Outbound state
        var el = document.getElementById('outbound-state');
        if (data.outbound_enabled) {{
          el.textContent = 'ON';
          el.className = 'cardValue outboundOn';
        }} else {{
          el.textContent = 'OFF';
          el.className = 'cardValue outboundOff';
        }}

        // Counts
        var inbound = document.getElementById('stat-inbound');
        if (inbound) inbound.textContent = (data.inbound_count !== null && data.inbound_count !== undefined) ? data.inbound_count : '—';
        var outcount = document.getElementById('stat-outbound-count');
        if (outcount) outcount.textContent = (data.outbound_count !== null && data.outbound_count !== undefined) ? data.outbound_count : '—';
        var unanswered = document.getElementById('stat-unanswered');
        if (unanswered) {{
          var u = data.unanswered_count;
          unanswered.textContent = (u !== null && u !== undefined) ? u : '—';
          unanswered.className = 'cardValue ' + threshClass(u, 5, 20);
        }}
        var needsHuman = document.getElementById('stat-needs-human');
        if (needsHuman) {{
          var nh = data.needs_human_count;
          needsHuman.textContent = (nh !== null && nh !== undefined) ? nh : '—';
          needsHuman.className = 'cardValue ' + threshClass(nh, 3, 10);
        }}
        var crit = document.getElementById('stat-critical');
        if (crit) {{
          var c = data.critical_count;
          crit.textContent = (c !== null && c !== undefined) ? c : '—';
          crit.className = 'cardValue ' + threshClass(c, 1, 5);
        }}

        // Latency
        var p50el = document.getElementById('stat-p50');
        if (p50el) p50el.textContent = fmtLatency(data.latency_p50_ms);
        var p95el = document.getElementById('stat-p95');
        if (p95el) p95el.textContent = fmtLatency(data.latency_p95_ms);
      }});
    }}

    // -------------------------------------------------------------------------
    // Threads table
    // -------------------------------------------------------------------------
    function fetchThreads() {{
      var url = '/api/ops/threads?limit=50';
      if (currentHealth && currentHealth !== 'all') url += '&health=' + encodeURIComponent(currentHealth);
      apiFetch(url, function(data) {{
        var rows = Array.isArray(data) ? data : (data.items || data.threads || []);
        var tbody = document.getElementById('threads-tbody');
        if (!rows.length) {{
          tbody.innerHTML = '<tr><td colspan="9" class="emptyState">Sin mensajes sin respuesta</td></tr>';
          return;
        }}
        tbody.innerHTML = rows.map(function(r) {{
          var cls = healthClass(r.health);
          var health = r.health ? ('<span class="badge ' + severityBadgeClass(r.health) + '">' + esc(r.health) + '</span>') : '—';
          var link = r.thread_id ? ('<a href="/whatsapp/thread/' + esc(r.thread_id) + '" target="_blank" rel="noopener">Ver</a>') : '—';
          var customerLabel = r.display_name || r.customer_name || r.wa_id_masked || String(r.thread_id || '—');
          var isWaitingCustomer = String(r.health || '').toUpperCase() === 'WAITING_CUSTOMER';
          return '<tr class="' + cls + '">'
            + '<td>' + esc(customerLabel) + '</td>'
            + '<td>' + fmtDateTime(r.latest_ts || r.last_activity_at || r.last_message_at) + '</td>'
            + '<td>' + dirBadge(r.latest_direction || r.last_direction) + '</td>'
            + '<td>' + esc(r.last_stage || r.stage || r.lead_stage) + '</td>'
            + '<td>' + (r.needs_human ? '<span class="badge badgeHigh">Sí</span>' : '—') + '</td>'
            + '<td>' + (isWaitingCustomer ? '<span class="badge badgeLow">Sí</span>' : '—') + '</td>'
            + '<td>' + fmtAge(r.waiting_seconds !== undefined ? r.waiting_seconds : r.age_seconds) + '</td>'
            + '<td>' + health + '</td>'
            + '<td>' + link + '</td>'
            + '</tr>';
        }}).join('');
      }});
    }}

    // -------------------------------------------------------------------------
    // Messages trace table (with expand detail)
    // -------------------------------------------------------------------------
    var _expandedRows = {{}};

    function fetchMessages() {{
      var url = '/api/ops/messages?limit=100&window=' + encodeURIComponent(currentWindow);
      if (currentDir) url += '&direction=' + encodeURIComponent(currentDir);
      apiFetch(url, function(data) {{
        var rows = Array.isArray(data) ? data : (data.items || data.messages || []);
        var tbody = document.getElementById('msgs-tbody');
        if (!rows.length) {{
          tbody.innerHTML = '<tr><td colspan="7" class="emptyState">Sin mensajes en este período</td></tr>';
          return;
        }}
        var html = '';
        rows.forEach(function(r, i) {{
          var rowId = 'msgrow-' + i;
          var detailId = 'msgdetail-' + i;
          // Preview: safe text node approach via esc()
          var preview = r.preview || r.text || r.body || r.content || '';
          if (preview.length > 80) preview = preview.substring(0, 80) + '…';

          // Path badge
          var pathLabel = esc(r.path_id || '—');
          var pathBadge;
          if (!r.path_id || String(r.path_id).toLowerCase() === 'unknown' ||
              String(r.path_id).toLowerCase().indexOf('legacy') >= 0) {{
            pathBadge = '<span class="badge badgeUnknown">' + pathLabel + '</span>';
          }} else {{
            pathBadge = '<span class="badge badgeLow">' + pathLabel + '</span>';
          }}

          // Status badge
          var st = r.status || '';
          var stBadge;
          if (st === 'delivered' || st === 'read') stBadge = '<span class="badge badgeLow">' + esc(st) + '</span>';
          else if (st === 'sent' || st === 'pending') stBadge = '<span class="badge badgeMedium">' + esc(st) + '</span>';
          else if (st === 'failed' || st === 'blocked') stBadge = '<span class="badge badgeCritical">' + esc(st) + '</span>';
          else stBadge = st ? esc(st) : '—';

          var customerLabel = r.display_name || r.customer_name || r.wa_id_masked || r.wa_id || '—';

          html += '<tr class="expandCursor" data-detail="' + detailId + '" onclick="toggleMsgDetail(this.dataset.detail)">'
            + '<td>' + fmtTime(r.timestamp || r.created_at) + '</td>'
            + '<td>' + dirBadge(r.direction) + '</td>'
            + '<td>' + esc(customerLabel) + '</td>'
            + '<td>' + esc(r.message_type || r.type) + '</td>'
            + '<td style="max-width:260px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="' + esc(preview) + '">' + esc(preview) + '</td>'
            + '<td>' + pathBadge + '</td>'
            + '<td>' + stBadge + '</td>'
            + '</tr>';

          // Detail row — no raw tokens or secrets
          var latencyStr = (r.latency_ms !== null && r.latency_ms !== undefined) ? fmtLatency(r.latency_ms) : '—';
          html += '<tr class="detailRow" id="' + detailId + '">'
            + '<td colspan="7"><div class="detailGrid">'
            + _detailItem('WAMID', r.wa_message_id)
            + _detailItem('ID interno', r.id)
            + _detailItem('Thread', r.thread_id)
            + _detailItem('Lead', r.lead_id)
            + _detailItem('WA ID', r.wa_id_masked)
            + _detailItem('Deployment', r.deployment_id)
            + _detailItem('Path', r.path_id)
            + _detailItem('Creado', r.timestamp)
            + _detailItem('Estado', r.status)
            + _detailItem('Bloqueado por', r.blocked_reason)
            + _detailItem('Latencia', latencyStr)
            + _detailItem('Correlation', r.correlation_id)
            + '</div></td></tr>';
        }});
        tbody.innerHTML = html;
      }});
    }}

    function _detailItem(key, val) {{
      return '<div class="detailItem"><div class="detailKey">' + esc(key) + '</div>'
           + '<div class="detailVal">' + esc(val !== null && val !== undefined ? val : '—') + '</div></div>';
    }}

    function toggleMsgDetail(id) {{
      var el = document.getElementById(id);
      if (!el) return;
      el.classList.toggle('open');
    }}

    // -------------------------------------------------------------------------
    // Critical events panel
    // -------------------------------------------------------------------------
    function fetchCritical() {{
      var url = '/api/ops/critical-events?limit=50&window=' + encodeURIComponent(currentWindow);
      apiFetch(url, function(data) {{
        var events = Array.isArray(data) ? data : (data.items || data.events || []);
        var container = document.getElementById('critical-list');
        if (!events.length) {{
          container.innerHTML = '<div class="criticalEmpty">Sin eventos críticos en este período</div>';
          return;
        }}
        container.innerHTML = events.map(function(ev) {{
          var sevCls = severityBadgeClass(ev.severity);
          return '<div class="eventItem">'
            + '<div class="eventTime">' + fmtTime(ev.created_at || ev.timestamp) + '</div>'
            + '<div class="eventBody">'
            + '<div class="eventType">'
            + '<span class="badge ' + sevCls + '">' + esc(ev.severity) + '</span> '
            + esc(ev.event_type || ev.type)
            + '</div>'
            + '<div class="eventDesc">' + esc(ev.description || ev.details_summary || '') + '</div>'
            + (ev.thread_id ? '<div class="eventMeta">Thread: ' + esc(ev.thread_id) + '</div>' : '')
            + '</div>'
            + '</div>';
        }}).join('');
      }});
    }}

    // -------------------------------------------------------------------------
    // Path monitoring table
    // -------------------------------------------------------------------------
    function fetchPaths() {{
      var url = '/api/ops/paths?window=' + encodeURIComponent(currentWindow);
      apiFetch(url, function(data) {{
        var rows = Array.isArray(data) ? data : (data.items || data.paths || []);
        var tbody = document.getElementById('paths-tbody');
        if (!rows.length) {{
          tbody.innerHTML = '<tr><td colspan="6" class="emptyState">Sin actividad de envíos en este período</td></tr>';
          return;
        }}
        tbody.innerHTML = rows.map(function(r) {{
          var isLegacy = !r.path_id || String(r.path_id).toLowerCase() === 'unknown' ||
                         String(r.path_id).toLowerCase().indexOf('legacy') >= 0;
          var rowCls = isLegacy ? 'rowCritical' : '';
          return '<tr class="' + rowCls + '">'
            + '<td><strong>' + esc(r.path_id || r.path) + '</strong></td>'
            + '<td>' + (r.total !== null && r.total !== undefined ? r.total : '—') + '</td>'
            + '<td>' + (r.success_count !== null && r.success_count !== undefined ? r.success_count : '—') + '</td>'
            + '<td>' + (r.blocked_count !== null && r.blocked_count !== undefined ? r.blocked_count : '—') + '</td>'
            + '<td>' + (r.failed_count !== null && r.failed_count !== undefined ? r.failed_count : '—') + '</td>'
            + '<td>' + pathStatusBadge(r.status, r.path_id) + '</td>'
            + '</tr>';
        }}).join('');
      }});
    }}

    // -------------------------------------------------------------------------
    // Orchestrate refresh
    // -------------------------------------------------------------------------
    function refreshAll() {{
      fetchSummary();
      fetchThreads();
      fetchMessages();
      fetchCritical();
      fetchPaths();
      document.getElementById('lastUpdated').textContent = nowHMS();
    }}

    // -------------------------------------------------------------------------
    // Sidebar collapse (localStorage persistence)
    // -------------------------------------------------------------------------
    function setSidebarCollapsed(collapsed) {{
      var sb = document.getElementById('sidebar');
      if (!sb) return;
      sb.classList.toggle('collapsed', collapsed);
      localStorage.setItem('sidebar_collapsed', collapsed ? '1' : '0');
    }}
    window.toggleSidebar = function() {{
      var sb = document.getElementById('sidebar');
      if (!sb) return;
      setSidebarCollapsed(!sb.classList.contains('collapsed'));
    }};

    // -------------------------------------------------------------------------
    // Boot
    // -------------------------------------------------------------------------
    (function init() {{
      // Restore sidebar state
      if (localStorage.getItem('sidebar_collapsed') === '1') {{
        setSidebarCollapsed(true);
      }}
      // Initial data load
      refreshAll();
      // Auto-refresh
      startAutoRefresh();
    }})();
  </script>
</body>
</html>"""
