# app/ui/kanban_view.py
from __future__ import annotations

from typing import Any
import logging
import json
import html as html_lib
from datetime import datetime, date, timedelta, time
from urllib.parse import urlencode

from sqlalchemy.orm import Session
from sqlalchemy import select

from ..models import Agencia, Lead, Revision, ViaticosZone, Profesional, Vendedor
from .components import render_sidebar_ai_block, render_sidebar_ai_script, render_sidebar_nav, render_whatsapp_icon_svg

logger = logging.getLogger(__name__)


# ---------- formatting helpers ----------

def _txt(v: Any) -> str:
    if v is None:
        return "-"
    if isinstance(v, str):
        s = v.strip()
        if s == "" or s.lower() == "string":
            return "-"
        return s
    return str(v)

def _val(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, str):
        s = v.strip()
        if s == "" or s.lower() == "string":
            return ""
        return s
    return str(v)

def _fmt_money(x: int | None) -> str:
    if x is None:
        return "-"
    return f"${x:,}".replace(",", ".")

def _safe_url(u: str | None) -> str | None:
    s = (u or "").strip()
    if not s or s.lower() == "string":
        return None
    return s

def _url_link(u: str | None, label: str = "Abrir") -> str:
    url = _safe_url(u)
    if not url:
        return "-"
    return f'<a href="{url}" target="_blank" rel="noopener">{label}</a>'


def _profesional_label(p: Profesional) -> str:
    name = f"{(p.nombre or '').strip()} {(p.apellido or '').strip()}".strip() or "-"
    cargo = (p.cargo or "").strip()
    return f"{name} ({cargo})" if cargo else name


def _sidebar_user_block(user_email: str) -> str:
    safe_email = html_lib.escape((user_email or "").strip() or "admin")
    return f"""
      <div class="sidebarFooter">
        {render_sidebar_ai_block()}
        <div class="sidebarUser">{safe_email}</div>
        <form method="post" action="/logout">
          <button class="logoutBtn" type="submit">Log Out</button>
        </form>
      </div>
      {render_sidebar_ai_script()}
    """


# ---------- icons ----------

ICON_SEARCH = '<svg class="icon icon-only" viewBox="0 0 24 24"><circle cx="11" cy="11" r="7"/><path d="M21 21l-4.3-4.3"/></svg>'
ICON_EXPORT = '<svg class="icon icon-only" viewBox="0 0 24 24"><path d="M12 3v12"/><path d="M8 7l4-4 4 4"/><path d="M4 15v4h16v-4"/></svg>'
ICON_PLUS_THIN = '<svg class="icon icon-only icon-thin-plus" viewBox="0 0 24 24"><path d="M12 5v14"/><path d="M5 12h14"/></svg>'
ICON_MENU_HAMBURGER = '<svg class="icon icon-only" viewBox="0 0 24 24"><path d="M4 7h16"/><path d="M4 12h16"/><path d="M4 17h16"/></svg>'
ICON_CLOSE = '<svg class="icon icon-only" viewBox="0 0 24 24"><path d="M6 6l12 12"/><path d="M18 6l-12 12"/></svg>'
ICON_CHEVRON_DOWN = '<svg class="icon icon-only" viewBox="0 0 24 24"><path d="M6 9l6 6 6-6"/></svg>'
ICON_ELLIPSIS = '<svg class="icon icon-only" viewBox="0 0 24 24"><circle cx="5" cy="12" r="1.6"/><circle cx="12" cy="12" r="1.6"/><circle cx="19" cy="12" r="1.6"/></svg>'
ICON_ARROW_LEFT = '<svg class="icon icon-only" viewBox="0 0 24 24"><path d="M14 6l-6 6 6 6"/><path d="M20 12H8"/></svg>'
ICON_ARROW_RIGHT = '<svg class="icon icon-only" viewBox="0 0 24 24"><path d="M10 6l6 6-6 6"/><path d="M4 12h12"/></svg>'
ICON_WHATSAPP = render_whatsapp_icon_svg()


def _base_css(extra_css: str = "") -> str:
    css = f"""
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <style>
      :root{{
        --bg:#f3f4f6;
        --card:#ffffff;
        --muted:#6b7280;
        --border:#e5e7eb;
        /* 4K-friendly scale tokens for kanban cards */
        --kanban-col-w: clamp({KANBAN_COLUMN_WIDTH_PX}px, calc({KANBAN_COLUMN_WIDTH_PX}px + 0.9vw), {int(KANBAN_COLUMN_WIDTH_PX * 1.12)}px);
        --card-w: clamp(210px, 8.4vw, 245px);
        --card-pad: clamp(8px, 0.46vw, 12px);
        --font-base: clamp(12px, 0.72vw, 14px);
        --font-sm: clamp(10px, 0.58vw, 12px);
        --chip-font: clamp(9px, 0.5vw, 11px);
        --radius: clamp(8px, 0.42vw, 12px);
        --gap: clamp(6px, 0.38vw, 10px);
        --shadow:0 2px 8px rgba(0,0,0,.08);
        --shadow2:0 6px 18px rgba(0,0,0,.08);
      }}
      body {{ font-family: Arial, sans-serif; margin: 0; background: var(--bg); font-size:var(--font-base); }}
      a {{ color:#2563eb; text-decoration: underline; }}

      .layout {{
        display:flex;
        min-height:100vh;
        position:relative;
        isolation:isolate;
      }}
      .layout::before {{
        content:"";
        position:fixed;
        inset:0;
        background-image: linear-gradient(180deg, rgba(255,255,255,.1), rgba(243,244,246,.15)), url('/static/bg.png');
        background-size: cover;
        background-position: center;
        background-repeat: no-repeat;
        pointer-events:none;
        z-index:-1;
      }}
      .sidebar{{
        width: 232px; background:#111827; color:#fff; padding:12px; position:sticky; top:0; height:100vh;
        transition: width .15s ease;
        display:flex;
        flex-direction:column;
      }}
      .sidebar.collapsed{{ width:68px; }}
      .brandRow{{ display:flex; align-items:center; justify-content:space-between; gap:8px; margin-bottom:12px; }}
      .brandText{{ font-weight:700; font-size:12px; letter-spacing:.3px; color:#e5e7eb; }}
      .sidebarToggle{{ border:none; background:transparent; color:#e5e7eb; cursor:pointer; padding:4px; border-radius:8px; }}
      .sidebarToggle:hover{{ background: rgba(255,255,255,.08); }}
      .nav a{{
        display:flex; align-items:center; gap:10px; padding:10px 10px; border-radius:10px; color:#e5e7eb; text-decoration:none; margin-bottom:8px;
      }}
      .nav {{
        padding-bottom:12px;
        border-bottom:1px solid rgba(255,255,255,.16);
      }}
      .navIcon{{ display:inline-flex; color:#fff; }}
      .waNavIcon{{ display:flex; align-items:center; justify-content:center; line-height:0; }}
      .navIcon svg{{ width:18px; height:18px; display:block; }}
      .waNavIcon svg{{ display:block; width:18px; height:18px; overflow:visible; }}
      .waNavIcon .icon-whatsapp{{ width:18px; height:18px; display:block; stroke:currentColor; fill:none; stroke-width:2; shape-rendering:geometricPrecision; vector-effect:non-scaling-stroke; }}
      .navLabel{{ white-space:nowrap; }}
      .sidebar.collapsed .navLabel{{ display:none; }}
      .sidebar.collapsed .nav a{{ justify-content:center; }}
      .nav a:hover{{ background: rgba(255,255,255,.08); }}
      .nav a.active{{ color:#fff; }}
      .nav a.active .icon, .nav a.active svg {{ filter: brightness(0) invert(1); }}
      .sidebarFooter {{
        margin-top:auto;
        padding-top:12px;
        padding-bottom:18px;
        border-top:1px solid rgba(255,255,255,.16);
      }}
      .sidebarAiBlock {{
        display:flex;
        flex-direction:column;
        gap:8px;
        margin-bottom:14px;
      }}
      .sidebarAiTitle {{
        font-size:12px;
        font-weight:700;
        letter-spacing:.2px;
        color:#f9fafb;
      }}
      .sidebarAiSubtitle {{
        font-size:11px;
        line-height:1.35;
        color:rgba(255,255,255,.8);
      }}
      .sidebarAiToggle {{
        position:relative;
        width:54px;
        height:30px;
        border:none;
        border-radius:999px;
        background:#ef4444;
        color:#fff;
        cursor:pointer;
        transition: background .18s ease, opacity .18s ease, box-shadow .18s ease;
        box-shadow: inset 0 0 0 1px rgba(255,255,255,.12);
      }}
      .sidebarAiToggle:hover:not(:disabled) {{
        box-shadow: inset 0 0 0 1px rgba(255,255,255,.18), 0 4px 10px rgba(0,0,0,.18);
      }}
      .sidebarAiToggle:focus-visible {{
        outline:2px solid rgba(255,255,255,.85);
        outline-offset:2px;
      }}
      .sidebarAiToggle:disabled {{
        cursor:wait;
      }}
      .sidebarAiToggle.is-on {{
        background:#22c55e;
      }}
      .sidebarAiToggle.is-off {{
        background:#ef4444;
      }}
      .sidebarAiToggle.is-loading {{
        opacity:.7;
      }}
      .sidebarAiGlyph {{
        position:absolute;
        top:50%;
        transform:translateY(-50%);
        font-size:12px;
        font-weight:700;
        line-height:1;
        opacity:.7;
        pointer-events:none;
      }}
      .sidebarAiGlyphOff {{
        right:10px;
      }}
      .sidebarAiGlyphOn {{
        left:10px;
      }}
      .sidebarAiToggle.is-on .sidebarAiGlyphOff {{
        opacity:0;
      }}
      .sidebarAiToggle.is-off .sidebarAiGlyphOn {{
        opacity:0;
      }}
      .sidebarAiKnob {{
        position:absolute;
        top:3px;
        left:3px;
        width:24px;
        height:24px;
        border-radius:50%;
        background:#fff;
        box-shadow:0 2px 8px rgba(15,23,42,.28);
        transition: transform .18s ease;
      }}
      .sidebarAiToggle.is-on .sidebarAiKnob {{
        transform:translateX(24px);
      }}
      .sidebarAiStatus {{
        min-height:14px;
        font-size:10px;
        color:rgba(255,255,255,.72);
      }}
      .sidebarAiStatus.is-error {{
        color:#fca5a5;
      }}
      .sidebarUser {{
        font-size:12px;
        color:#d1d5db;
        margin-bottom:8px;
        overflow:hidden;
        text-overflow:ellipsis;
        white-space:nowrap;
      }}
      .logoutBtn {{
        width:100%;
        border:1px solid rgba(255,255,255,.24);
        background:transparent;
        color:#f9fafb;
        border-radius:8px;
        padding:7px 8px;
        cursor:pointer;
      }}
      .logoutBtn:hover {{ background: rgba(255,255,255,.1); }}
      .sidebar.collapsed .sidebarAiBlock {{
        display:none;
      }}
      .sidebar.collapsed .sidebarFooter {{ display:none; }}
      .main{{
        flex:1; padding:clamp(18px, 1vw, 28px);
        background:transparent;
        position:relative;
        z-index:1;
      }}
      .kanbanTopBar {{
        position: sticky;
        top: 0;
        z-index: 35;
        display:flex;
        align-items:center;
        justify-content:space-between;
        gap:12px;
        background:#fff;
        border:1px solid var(--border);
        border-radius: var(--radius);
        box-shadow: var(--shadow);
        padding: clamp(8px, 0.5vw, 12px) clamp(10px, 0.8vw, 16px);
        margin-bottom: 12px;
      }}
      .kanbanTopBarTitle {{
        font-size: clamp(18px, 1.08vw, 24px);
        font-weight: 700;
        color:#111827;
      }}
      .kanbanTopBarRight {{
        display:flex;
        align-items:center;
        gap:8px;
        min-width:0;
      }}
      .buildStamp {{
        font-size: var(--font-sm);
        color:#4b5563;
        white-space: nowrap;
      }}
      .searchControl {{
        display:flex;
        align-items:center;
        gap:6px;
        min-width:0;
      }}
      .searchBoxWrap {{
        display:flex;
        align-items:center;
        gap:6px;
        width:0;
        opacity:0;
        overflow:hidden;
        transition: width .2s ease, opacity .18s ease;
      }}
      .searchControl.open .searchBoxWrap {{
        width: clamp(220px, 26vw, 450px);
        opacity:1;
      }}
      .searchInput {{
        width:100%;
        height:34px;
      }}
      .searchCount {{
        font-size: var(--font-sm);
        color:#4b5563;
        white-space:nowrap;
      }}

      h1 {{ margin: 0 0 12px 0; }}
      .muted {{ color: var(--muted); font-size: var(--font-sm); }}

      .board {{ display: flex; flex-direction:row; flex-wrap:nowrap; gap: var(--gap); align-items: flex-start; overflow-x: auto; overflow-y: visible; padding-bottom: 10px; scrollbar-gutter: stable; position:relative; z-index:1; }}
      .board::-webkit-scrollbar {{ height: 10px; }}
      .board::-webkit-scrollbar-thumb {{ background: #d1d5db; border-radius: 999px; }}
      .board::-webkit-scrollbar-track {{ background: #f3f4f6; }}
      .kanban-column {{ flex:0 0 var(--kanban-col-w); flex-shrink:0; width:var(--kanban-col-w); min-width:var(--kanban-col-w); max-width:var(--kanban-col-w); background: rgba(255,255,255,.55); border: 1px solid var(--border); border-radius: var(--radius); padding: var(--card-pad); box-shadow: var(--shadow); overflow: visible; }}
      .kanban-column h2 {{ font-size: clamp(13px, 0.72vw, 16px); margin: 0 0 var(--gap) 0; display:flex; justify-content:space-between; align-items:center; }}
      .badge {{ font-size: var(--chip-font); color: #1f2937; background:#dbeafe; border:1px solid #93c5fd; padding:2px 7px; border-radius:999px; font-weight:700; }}

      .card {{ background: var(--card); border: 1px solid var(--border); border-radius: var(--radius); padding: var(--card-pad); margin-bottom: var(--gap); box-shadow: var(--shadow); overflow: visible; }}
      .leadCard {{ padding: clamp(7px, 0.4vw, 10px); }}
      .card:hover{{ box-shadow: var(--shadow2); }}
      .card.human {{ border-color: #f59e0b; box-shadow: 0 0 0 2px rgba(245,158,11,.18), var(--shadow); }}
      .card.humanAlert {{ border: 2px solid #ef4444; }}
      .card.flash {{ box-shadow: 0 0 0 4px rgba(37,99,235,.55), 0 0 24px rgba(37,99,235,.25), var(--shadow2); transition: box-shadow .3s ease; }}
      .leadCard.dragging {{ opacity:.65; transform: scale(.995); }}
      .row {{ display:flex; justify-content:space-between; gap:var(--gap); align-items:flex-start; }}

      .leftRow {{ display:flex; gap:var(--gap); align-items:center; }}
      .cardHeaderRow, .card-header {{ display:flex; flex-direction:column; justify-content:flex-start; align-items:stretch; gap:8px; padding:clamp(6px, 0.35vw, 9px) clamp(8px, 0.44vw, 11px); border-radius:var(--radius); border:1px solid transparent; background:#f5f5f5; cursor:default; }}
      .cardHeaderRow[data-drag-handle="1"] {{ cursor:pointer !important; }}
      .cardHeaderRow[data-drag-handle="1"]:active {{ cursor:pointer !important; }}
      .cardHeaderRow.flag-PRESUPUESTANDO {{ background:#fef3c7; border-color:#fcd34d; }}
      .cardHeaderRow.flag-PRESUPUESTO_ENVIADO {{ background:#e0f2fe; border-color:#7dd3fc; }}
      .cardHeaderRow.flag-ACEPTADO {{ background:#dcfce7; border-color:#86efac; }}
      .cardHeaderRow.flag-RECOMPRA {{ background:#e0e7ff; border-color:#a5b4fc; }}
      .cardHeaderRow.flag-PERDIDO {{ background:#fee2e2; border-color:#fca5a5; }}
      .cardHeaderTop {{ display:flex; align-items:center; justify-content:space-between; gap:8px; min-width:0; }}
      .lead-head {{ display:flex; align-items:center; justify-content:space-between; gap:12px; min-width:0; width:100%; }}
      .lead-head-left, .lead-head-right {{ display:flex; align-items:center; gap:10px; min-width:0; }}
      .lead-head-left {{ flex:1 1 auto; }}
      .lead-head-right {{ flex:0 0 auto; margin-left:auto; }}
      .lead-head-left form{{ display:flex; align-items:center; margin:0; }}
      .lead-head-right .pill{{ align-self:center; }}
      .lead-head-right .menu{{ display:inline-flex; align-items:center; }}
      .lead-head-right .menu > summary{{ display:flex; align-items:center; justify-content:center; line-height:0; }}
      .iconBtn{{ display:inline-flex; align-items:center; justify-content:center; line-height:0; }}
      .cardHeaderTopLeft {{ display:flex; align-items:center; gap:6px; min-width:0; flex-wrap:wrap; }}
      .cardHeaderBottom {{ display:flex; align-items:center; gap:6px; min-width:0; flex-wrap:wrap; }}
      .cardHeaderLeft, .card-header-left {{ display:flex; align-items:center; flex-wrap:wrap; gap:6px; min-width:0; max-width:100%; flex:1 1 auto; }}
      .cardHeaderMeta, .leadHeaderMeta {{ display:flex; align-items:center; flex-wrap:wrap; gap:6px; min-width:0; max-width:100%; }}
      .cardHeaderRight, .card-header-right {{ display:flex; align-items:center; gap:8px; flex-wrap:wrap; margin-left:auto; min-width:0; }}
      .revBox {{ position:relative; }}
      .revBox > summary {{ padding-right:44px; }}
      .revSummary {{ display:block; min-height:24px; padding-right:42px; }}
      .revBox .revMenu {{ position:absolute; top:10px; right:10px; z-index:1300; overflow:visible; }}
      .revEditWrap {{ width:100%; max-width:100%; overflow:hidden; }}
      .revEditPanel {{ width:100%; max-width:100%; max-height:80vh; overflow:auto; }}
      /* --- Fix revision edit panel overflow --- */
      .revEditPanel,
      .revEditPanel * {{
        box-sizing: border-box;
      }}

      .revEditPanel .grid {{
        display: grid;
        grid-template-columns: 1fr 1fr;
        gap: 10px;
      }}

      .revEditPanel .grid > div {{
        min-width: 0;
      }}

      .revModalOverlay {{
        position: fixed;
        inset: 0;
        background: rgba(17, 24, 39, .45);
        display: none;
        align-items: center;
        justify-content: center;
        padding: 12px;
        z-index: 5000;
      }}
      .revModalOverlay.open {{ display:flex; }}
      .revModal {{
        width: min(900px, 70vw);
        max-width: calc(100vw - 24px);
        height: min(80vh, 720px);
        max-height: calc(100vh - 24px);
        background:#fff;
        border:1px solid var(--border);
        border-radius:14px;
        box-shadow: var(--shadow2);
        display:flex;
        flex-direction:column;
        overflow:hidden;
        position: relative;
        z-index: 5001;
      }}
      .revModalHead {{
        display:flex;
        align-items:center;
        justify-content:space-between;
        gap:10px;
        padding:12px 14px;
        border-bottom:1px solid var(--border);
      }}
      .revModalTitle {{
        font-size:clamp(14px, .85vw, 18px);
        font-weight:700;
        color:#111827;
      }}
      .revModalBody {{
        flex:1 1 auto;
        overflow:auto;
        padding: 14px;
      }}
      .revModalFooter {{
        display:flex;
        justify-content:flex-end;
        gap:8px;
        padding:10px 14px;
        border-top:1px solid var(--border);
        background:#fff;
      }}
      .revEditPanel {{
        margin:0;
        border:none;
        background:transparent;
        box-shadow:none;
        width:100%;
        max-width:none;
        padding:0;
      }}
      .revEditPanel input,
      .revEditPanel select,
      .revEditPanel textarea {{
        width: 100%;
        min-width: 0;
      }}

      @media (max-width: 900px) {{
        .revModal {{
          width: calc(100vw - 24px);
          height: min(88vh, 720px);
        }}
      }}
      @media (max-width: 768px) {{
        .revModal {{
          position: fixed;
          top: 104px;
          left: 0;
          right: 0;
          bottom: 0;
          width: 100% !important;
          max-width: 100% !important;
          height: auto !important;
          max-height: none !important;
          border-radius: 14px 14px 0 0;
        }}
      }}
      @media (max-width: 520px) {{
        .revEditPanel .grid {{
          grid-template-columns: 1fr;
        }}
      }}
      .pill {{ display:inline-flex; align-items:center; padding:4px 8px; border-radius:999px; white-space:nowrap; font-size:var(--chip-font); font-weight:700; border:1px solid var(--border); background:#f9fafb; min-width:0; max-width:100%; overflow:hidden; text-overflow:ellipsis; }}
      .pill-veh {{ background:#eef2ff; border-color:#c7d2fe; }}
      .pill-count {{ background:#ecfeff; border-color:#a5f3fc; }}
      .pill-prof {{ background:#ecfdf3; border-color:#86efac; color:#166534; }}
      .pill-approval-pending {{ background:#fffbeb; border-color:#fcd34d; color:#92400e; }}
      .pill-approval-pending:hover {{ background:#f0c040; color:#7a5c00; }}
      .pill-approval-approved {{ background:#ecfdf3; border-color:#86efac; color:#166534; }}
      .pill-approval-rejected {{ background:#fef2f2; border-color:#fca5a5; color:#991b1b; }}
      .leadIdBadge {{
        display:inline-flex;
        align-items:center;
        gap:6px;
        background:#8b1b1b;
        color:#fff;
        border:none;
        border-radius:var(--radius);
        padding:6px 10px;
        font-weight:700;
      }}
      .leadIdBadge .icon{{ width:13px; height:13px; margin-right:0; }}
      .leadWaBtn {{
          display:inline-flex;
          align-items:center;
          justify-content:center;
        width:34px;
        height:34px;
        border:1px solid #cbd5e1;
        border-radius:10px;
        background:#fff;
        color:#0f766e;
          cursor:pointer;
          padding:0;
        }}
      .lead-head-left .leadWaBtn{{ width:34px; height:34px; min-height:34px; flex:0 0 34px; align-self:center; }}
      .waIconBtn{{ display:flex; align-items:center; justify-content:center; line-height:0; }}
      .waIconBtn svg, .waNavIcon svg{{ display:block; width:18px; height:18px; overflow:visible; }}
      .leadWaBtn .icon{{ width:16px; height:16px; margin-right:0; }}
      .leadWaBtn .icon-whatsapp{{ width:16px; height:16px; display:block; stroke:currentColor; fill:none; stroke-width:2; vector-effect:non-scaling-stroke; }}
      .leadWaBtn.active{{ background:#dcfce7; border-color:#86efac; color:#166534; }}
      .leadWaBtn:hover{{ background:#f8fafc; }}
      body[data-debug-icons="1"] .waNavIcon,
      body[data-debug-icons="1"] .waIconBtn{{ outline:1px dashed rgba(239,68,68,.65); }}
      body[data-debug-icons="1"] .waNavIcon svg,
      body[data-debug-icons="1"] .waIconBtn svg{{ outline:1px solid rgba(37,99,235,.6); }}
      .leadStatus {{
        margin-top:6px;
        font-size:clamp(12px, .66vw, 15px);
        font-weight:700;
        color:#991b1b;
      }}
      .leadStatus.status-default {{ color:#374151; }}
      .leadNameRow {{
        display:flex;
        align-items:center;
        gap:6px;
        font-size:clamp(15px, .82vw, 20px);
        line-height:1.25;
        font-weight:600;
        margin-top:3px;
      }}
      .leadToggle {{
        border:none;
        background:transparent;
        padding:0;
        display:inline-flex;
        align-items:center;
        gap:6px;
        cursor:pointer;
        color:inherit;
        font:inherit;
      }}
      .leadCaret {{ display:inline-block; font-size:clamp(12px, .7vw, 16px); opacity:.75; transition: transform .2s ease; }}
      .leadToggle[aria-expanded="true"] .leadCaret {{ transform: rotate(180deg); }}
      .leadDetailsBody {{
        overflow:hidden;
        max-height:0;
        opacity:0;
        transform:translateY(-4px);
        transition:max-height .28s ease, opacity .2s ease, transform .2s ease;
      }}
      .leadDetailsBody.open {{
        max-height:var(--lead-details-max, 760px);
        opacity:1;
        transform:translateY(0);
      }}
      .leadRevPanel {{
        margin-top:4px;
        background:#f9fafb;
        border:1px solid #e5e7eb;
        border-radius:var(--radius);
        padding:clamp(8px, .45vw, 12px);
      }}
      .leadRevTitle {{ font-weight:700; font-size:clamp(12px, .72vw, 15px); letter-spacing:.2px; }}
      .leadRevTotal {{ font-weight:700; font-size:clamp(13px, .78vw, 16px); margin-top:4px; color:#111827; }}
      .leadRevLines {{ margin-top:4px; color:#6b7280; }}
      .leadContact {{ margin-top:4px; }}
      .leadVehicleRow {{ margin-top:7px; display:flex; flex-wrap:wrap; gap:6px; }}
      .pill-gray {{ background:#e5e7eb; border-color:#d1d5db; color:#1f2937; }}
      .kanbanCol.drag-over {{ outline:2px dashed #94a3b8; outline-offset:4px; }}
      .dropPlaceholder {{ border:2px dashed #93c5fd; border-radius:var(--radius); margin:6px 0; background:rgba(147,197,253,.12); min-height:64px; }}
      .flagPill {{ display:inline-flex; align-items:center; padding:2px 8px; border-radius:999px; font-size:11px; font-weight:600; border:1px solid transparent; }}
      .flag-PRESUPUESTANDO {{ background:#fef3c7; border-color:#fcd34d; color:#92400e; }}
      .flag-PRESUPUESTO_ENVIADO {{ background:#e0f2fe; border-color:#7dd3fc; color:#075985; }}
      .flag-ACEPTADO {{ background:#dcfce7; border-color:#86efac; color:#166534; }}
      .flag-RECOMPRA {{ background:#e0e7ff; border-color:#a5b4fc; color:#3730a3; }}
      .flag-PERDIDO {{ background:#fecaca; border-color:#ef4444; color:#7f1d1d; font-weight:800; }}

      .btn {{ display:inline-flex; align-items:center; justify-content:center; padding: clamp(5px, .32vw, 8px) clamp(8px, .5vw, 11px); border-radius: 8px; border: 1px solid #d1d5db; background:#fff; cursor:pointer; text-decoration:none; color:#111827; font-size:var(--font-sm); font-weight:600; }}
      .btn-sm {{ padding: 3px 7px; font-size: var(--chip-font); border-radius: 7px; }}
      .btn:hover {{ background:#f9fafb; }}
      .btn-primary {{ border-color: #2563eb; }}
      .btn-danger {{ border-color: #ef4444; }}

      .iconBtn{{ border:none; background:transparent; cursor:pointer; font-size:clamp(13px, 0.72vw, 16px); padding:2px 3px; border-radius:7px; }}
      .iconBtn:hover{{ background:#f3f4f6; }}
      .icon{{ width:12px; height:12px; vertical-align:-2px; margin-right:5px; stroke:currentColor; fill:none; stroke-width:2; stroke-linecap:round; stroke-linejoin:round; }}
      .icon-only{{ margin-right:0; }}
      .icon-thin-plus{{ stroke-width:1.5; }}
      .addLeadSummary{{ display:inline-flex; align-items:center; gap:6px; }}
      .addLeadSummary .icon{{ width:13px; height:13px; }}
      .addLeadError{{ margin-top:8px; color:#b91c1c; font-size:var(--font-sm); }}
      .search-item-hidden{{ display:none !important; }}
      body.modal-open{{ overflow:hidden; }}

      .stack {{ display:flex; gap:8px; flex-wrap:wrap; margin-top: 10px; }}

      select, input, textarea {{
        padding: 8px; border-radius: 10px; border: 1px solid #d1d5db; width: 100%; box-sizing: border-box;
      }}
      textarea {{ min-height: 70px; }}
      .headerFlag {{
        display:inline-flex;
        align-items:center;
        padding:2px 8px;
        border-radius:999px;
        border:1px solid #d1d5db;
        background:#f3f4f6;
        color:#1f2937;
        font-size:var(--chip-font);
        font-weight:700;
      }}
      .cardHeaderRow.flag-PRESUPUESTANDO .headerFlag {{ background:#fef3c7; border-color:#fcd34d; color:#92400e; }}
      .cardHeaderRow.flag-PRESUPUESTO_ENVIADO .headerFlag {{ background:#e0f2fe; border-color:#7dd3fc; color:#075985; }}
      .cardHeaderRow.flag-ACEPTADO .headerFlag {{ background:#dcfce7; border-color:#86efac; color:#166534; }}
      .cardHeaderRow.flag-RECOMPRA .headerFlag {{ background:#e0e7ff; border-color:#a5b4fc; color:#3730a3; }}
      .cardHeaderRow.flag-PERDIDO .headerFlag {{ background:#fecaca; border-color:#ef4444; color:#7f1d1d; }}

      details {{ margin-top: 10px; }}
      summary {{ cursor: pointer; font-weight: bold; }}

      .grid {{ display:grid; grid-template-columns: 1fr 1fr; gap: 8px; }}
      .grid-1 {{ display:grid; grid-template-columns: 1fr; gap: 8px; }}
      .box {{ background:#f9fafb; border:1px solid var(--border); border-radius:14px; padding:10px; margin-top:10px; }}
      .rev {{ margin-top: 10px; padding-top: 10px; border-top: 1px solid var(--border); min-width:0; max-width:100%; overflow:visible; }}
      .revHead {{ display:flex; flex-direction:column; gap:6px; min-width:0; max-width:100%; overflow:hidden; }}
      .revHeadLine1 {{ display:flex; align-items:center; justify-content:space-between; gap:8px; min-width:0; max-width:100%; flex-wrap:wrap; overflow:hidden; }}
      .revHeadTitle {{ font-weight:700; color:#111827; min-width:0; max-width:100%; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }}
      .revHeadTurno {{ display:inline-flex; align-items:center; min-width:0; max-width:100%; font-size:var(--font-sm); color:#4b5563; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }}
      .revHeadLine2 {{ display:flex; align-items:center; gap:8px; min-width:0; max-width:100%; flex-wrap:wrap; overflow:hidden; }}
      .revHeadLine2 .pill-prof {{ max-width:100%; }}
      .revHeadLine3 {{ display:flex; align-items:center; gap:8px; min-width:0; max-width:100%; flex-wrap:wrap; overflow:hidden; }}
      .revEstadoPill {{ background:#eef2ff; border-color:#c7d2fe; color:#1e3a8a; }}
      .revApprovalRow {{ display:flex; gap:8px; flex-wrap:wrap; margin-top:8px; }}
      .revApprovalRow {{ align-items:center; }}

      .label {{ font-size: var(--font-sm); color:#374151; margin-bottom: 4px; }}
      .small {{ font-size: var(--font-sm); }}

      .menu {{ position: relative; display:inline-block; z-index:1300; overflow:visible; }}
      .menu > summary {{ list-style:none; }}
      .menu > summary::-webkit-details-marker {{ display:none; }}
      /* KANBAN POPOVER */
      .menuPanel{{
        position:absolute; right:0; top:28px; z-index:1800;
        width: min(350px, 92vw); max-width: 92vw; background:#fff; border:1px solid var(--border); border-radius:14px; box-shadow: var(--shadow2);
        padding:10px;
      }}
      .menuPanel.align-left{{ left:0; right:auto; }}
      .menuPanel.portal{{ position:fixed; z-index:1800; max-width:min(350px, calc(100vw - 16px)); }}
      #popover-root{{ position:fixed; inset:0; pointer-events:none; z-index:1800; overflow:visible; }}
      #popover-root .menuPanel{{ pointer-events:auto; }}
      .menuTitle{{ font-weight:700; font-size:13px; margin-bottom:8px; }}
      .divider{{ height:1px; background:var(--border); margin:10px 0; }}

      .danger-note {{ font-size: 11px; color:#ef4444; }}
      .totalPresu {{ font-size: 15px; font-weight:700; margin-top:8px; color:#111827; }}
      .menuInline {{
        border:1px solid var(--border);
        border-radius:12px;
        padding:10px;
        background:#f9fafb;
        margin-top:8px;
      }}
      .menuInline .menuInlineActions {{ display:flex; gap:8px; margin-top:8px; }}
      .menuEstadoQuick {{ margin-top:8px; margin-bottom:2px; }}
      .menuEstadoQuick .label {{ margin-bottom:6px; }}
      .hidden {{ display:none !important; }}
      .leadCard.search-hidden {{ display:none !important; }}
      .toastWrap {{
        position: fixed;
        left: 50%;
        bottom: 18px;
        transform: translateX(-50%);
        z-index: 1300;
      }}
      .toast {{
        background:#111827;
        color:#fff;
        border-radius:12px;
        padding:12px 16px;
        box-shadow: var(--shadow2);
        display:flex;
        align-items:center;
        gap:10px;
        font-size:var(--font-sm);
      }}
      .toast button {{
        border:none;
        border-radius:10px;
        padding:6px 10px;
        background:#facc15;
        color:#111827;
        font-weight:700;
        cursor:pointer;
      }}

      .filters {{ margin: 12px 0; }}
      .drawerOverlay{{
        position:fixed; inset:0; background:rgba(0,0,0,.2);
        opacity:0; pointer-events:none; transition:opacity .15s ease; z-index:40;
      }}
      .drawer{{
        position:fixed; right:0; top:0; height:100%; width:360px; max-width:92vw;
        background:#fff; border-left:1px solid var(--border); box-shadow: var(--shadow2);
        transform:translateX(100%); transition:transform .2s ease; z-index:41; padding:14px;
      }}
      .drawer.open{{ transform:translateX(0); }}
      .drawerOverlay.open{{ opacity:1; pointer-events:auto; }}

      /* Multi-select dropdown (Estado) */
      .multiSelect {{ position: relative; }}
      .multiSelect > summary {{
        list-style: none;
        cursor: pointer;
        user-select: none;

        height: 38px;
        padding: 0 12px;
        border-radius: 12px;
        border: 1px solid var(--border);
        background: #fff;

        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 10px;
      }}
      .multiSelect > summary::-webkit-details-marker {{ display:none; }}
      .multiSelect .msValue {{ min-width:0; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; color:#111827; }}
      .multiSelect .msCaret {{ opacity:.7; }}

      .multiSelect[open] > summary {{ box-shadow: 0 8px 24px rgba(0,0,0,.10); }}

      .multiSelect .msPanel {{
        position: absolute;
        z-index: 50;
        top: calc(100% + 6px);
        left: 0;
        right: 0;

        background: #fff;
        border: 1px solid var(--border);
        border-radius: 12px;
        box-shadow: 0 12px 30px rgba(0,0,0,.12);
        padding: 10px;
        max-height: 240px;
        overflow: auto;
      }}

      .msItem {{
        display:flex;
        align-items:center;
        gap:10px;
        padding: 8px 8px;
        border-radius: 10px;
        font-size: 13px;
      }}
      .msItem:hover {{ background:#f3f4f6; }}
      .msItem input {{ width:16px; height:16px; }}
      .kanban-mobile-tabs {{ display:none; }}

      @media (max-width: 420px) {{
        .estadoGrid {{ grid-template-columns: 1fr; }}
      }}

      .filterActionsRow{{ display:flex; gap:10px; align-items:stretch; margin-top:10px; }}
      .filterActionsRow .btn{{
        height: 38px !important;
        padding: 0 14px !important;
        display: inline-flex !important;
        align-items: center !important;
        justify-content: center !important;
        box-sizing: border-box !important;
      }}

      .backLink{{
        display:inline-flex;
        align-items:center;
        gap:6px;
        margin-top:12px;
        padding:4px 6px;
        border-radius:8px;
        color:#111827;
        text-decoration:none;
        font-weight:600;
        font-size:13px;
      }}
      .backLink:hover{{ background:#f3f4f6; }}
      .backLink .arrow{{ font-size:14px; line-height:1; opacity:.75; }}

      .rev-highlight {{
        box-shadow: 0 0 0 2px rgba(37,99,235,.45), var(--shadow);
        background: #eef2ff;
        transition: box-shadow .3s ease, background .3s ease;
      }}

      @media (max-width: 768px) {{
        html {{ scroll-behavior: smooth; scroll-padding-top: 110px; }}
        /* Fix 1: sidebar → fixed top icon bar */
        .sidebar {{
          position: fixed;
          top: 0; left: 0; right: 0;
          width: 100% !important;
          height: 52px;
          min-height: 52px;
          flex-direction: row;
          align-items: center;
          padding: 0 8px;
          z-index: 100;
          overflow: hidden;
        }}
        .sidebar.collapsed {{
          width: 100% !important;
          height: 52px;
        }}
        .brandRow, .sidebarToggle, .sidebarFooter {{ display: none !important; }}
        .nav {{
          display: flex !important;
          flex-direction: row;
          align-items: center;
          flex: 1;
          gap: 0;
          padding-bottom: 0;
          border-bottom: none;
          justify-content: space-around;
          width: 100%;
        }}
        .nav a {{
          flex-direction: column;
          padding: 6px;
          margin-bottom: 0;
          gap: 2px;
          flex: 1;
          justify-content: center;
          align-items: center;
        }}
        .nav a.active {{ color:#fff; }}
        .nav a.active .icon, .nav a.active svg {{ filter: brightness(0) invert(1); }}
        .navLabel {{ display: none !important; }}
        .kanbanTopBar {{ display: none; }}
        .main {{
          padding-top: 0;
          padding-left: 0;
          padding-right: 0;
          overflow-x: hidden;
        }}
        /* board: horizontal snap scroll */
        .board {{
          display: flex;
          flex-direction: row;
          overflow-x: scroll;
          overflow-y: visible;
          scroll-snap-type: x mandatory;
          width: 100vw;
          scroll-behavior: smooth;
          -webkit-overflow-scrolling: touch;
          gap: 0;
          padding: 0;
          margin-bottom: 0;
          padding-bottom: 0;
          box-sizing: border-box;
          background: var(--bg);
          min-height: calc(100svh - 104px);
          align-items: stretch;
        }}
        .kanban-column {{
          flex: 0 0 100vw;
          width: 100vw;
          min-width: 100vw;
          max-width: 100vw;
          scroll-snap-align: start;
          scroll-margin-top: 100px;
          overflow-y: auto;
          overflow-x: hidden;
          min-height: calc(100svh - 104px);
          box-sizing: border-box;
          padding: 52px 12px 100px;
          border-radius: 0;
          border-left: none;
          border-right: none;
        }}
        .kanban-column-cards {{ margin-top: var(--gap); }}
        .card, .leadCard {{
          width: 100%;
          box-sizing: border-box;
          overflow: hidden;
        }}
        .cardHeaderRow, .card-header {{
          box-sizing: border-box;
          max-width: 100%;
          overflow: hidden;
        }}
        .lead-head {{
          box-sizing: border-box;
          width: 100%;
        }}
        /* sticky mobile tab bar */
        .kanban-mobile-tabs {{
          display: flex;
          align-items: stretch;
          position: sticky;
          top: 52px;
          z-index: 30;
          height: 52px;
          margin-top: 0;
          background: var(--bg);
          border-bottom: 1px solid var(--border);
          box-shadow: 0 2px 8px rgba(0,0,0,.06);
        }}
        .kanban-mobile-tabs-inner {{
          display: flex;
          align-items: center;
          overflow-x: auto;
          scrollbar-width: none;
          gap: 6px;
          padding: 0 8px;
          width: 100%;
        }}
        .kanban-mobile-tabs-inner::-webkit-scrollbar {{ display: none; }}
        .kanban-tab {{
          flex: 0 0 auto;
          display: inline-flex;
          align-items: center;
          gap: 6px;
          padding: 5px 12px;
          border-radius: 999px;
          border: 1px solid var(--border);
          background: #f9fafb;
          color: #111827;
          font-size: 12px;
          font-weight: 600;
          white-space: nowrap;
          cursor: pointer;
          text-decoration: none;
        }}
        .kanban-tab:hover {{ background: #eef2f7; }}
        .kanban-tab.active {{
          background: #111827;
          color: #fff;
          border-color: #111827;
        }}
      }}

      {extra_css}
    </style>
    """
    return css


def _build_query_string(params: dict[str, Any]) -> str:
    pairs: list[tuple[str, str]] = []
    for key, value in params.items():
        if isinstance(value, list):
            for v in value:
                sv = str(v).strip()
                if sv:
                    pairs.append((key, sv))
            continue
        if value is None:
            continue
        sv = str(value).strip()
        if sv:
            pairs.append((key, sv))
    return urlencode(pairs, doseq=True)


def _filters_form_html(
    *,
    q: str,
    estado: list[str] | None,
    flag: list[str] | None,
    profesional_id: str,
    profesionales: list[Profesional] | None,
    canal: str,
    tipo_vehiculo: str,
    marca: str,
    modelo: str,
    anio: str,
    zone_group: str,
    zone_detail: str,
    estado_revision: str,
    from_date: str,
    to_date: str,
    date_field: str,
    zones_map: dict[str, list[str]] | None,
    action: str,
    include_back_link: bool = False,
    back_href: str = "/kanban",
    include_open_filters: bool = False,
) -> str:
    zones_map = zones_map or {}
    has_zones = bool(zones_map)
    zone_groups = sorted(zones_map.keys()) if has_zones else []
    zone_group_val = _val(zone_group)
    zone_detail_val = _val(zone_detail)

    if has_zones:
        zone_group_options = "".join(
            f'<option value="{g}" {"selected" if g == zone_group_val else ""}>{g}</option>'
            for g in zone_groups
        )
        zone_detail_options = "".join(
            f'<option value="{d}" {"selected" if d == zone_detail_val else ""}>{d}</option>'
            for d in (zones_map.get(zone_group_val) or [])
        )
        zone_inputs_html = f"""
          <div class="grid" style="margin-top:8px;">
            <div>
              <div class="label">Zona grupo</div>
              <select name="zone_group" data-zone-group="1">
                <option value="">-</option>
                {zone_group_options}
              </select>
            </div>
            <div>
              <div class="label">Zona detalle</div>
              <select name="zone_detail" data-zone-detail="1">
                <option value="">-</option>
                {zone_detail_options}
              </select>
            </div>
          </div>
        """
    else:
        zone_inputs_html = f"""
          <div class="grid" style="margin-top:8px;">
            <div>
              <div class="label">Zona grupo</div>
              <input name="zone_group" value="{zone_group_val}"/>
            </div>
            <div>
              <div class="label">Zona detalle</div>
              <input name="zone_detail" value="{zone_detail_val}"/>
            </div>
          </div>
        """

    estado_set = set(estado or [])
    selected_labels = [KANBAN_LABELS.get(k, k) for k in KANBAN_ORDER if k in estado_set]
    if not selected_labels:
        estado_label = "-"
    elif len(selected_labels) == 1:
        estado_label = selected_labels[0]
    else:
        estado_label = f"{selected_labels[0]}, +{len(selected_labels) - 1}"

    estado_checks = "".join(
        f'<label class="msItem"><input type="checkbox" name="estado" value="{k}" '
        f'{"checked" if k in estado_set else ""}/> <span>{KANBAN_LABELS.get(k, k)}</span></label>'
        for k in KANBAN_ORDER
    )

    estado_html = f"""
      <div>
        <div class="label">Estado</div>
        <details class="multiSelect">
          <summary>
            <span class="msValue">{estado_label}</span>
            <span class="msCaret">{ICON_CHEVRON_DOWN}</span>
          </summary>
          <div class="msPanel">
            {estado_checks}
          </div>
        </details>
      </div>
    """

    flag_set = set(flag or [])
    flag_selected_labels = [FLAG_LABELS.get(k, k) for k in FLAG_VALUES if k in flag_set]
    if not flag_selected_labels:
        flag_label = "-"
    elif len(flag_selected_labels) == 1:
        flag_label = flag_selected_labels[0]
    else:
        flag_label = f"{flag_selected_labels[0]}, +{len(flag_selected_labels) - 1}"

    flag_checks = "".join(
        f'<label class="msItem"><input type="checkbox" name="flag" value="{k}" '
        f'{"checked" if k in flag_set else ""}/> <span>{FLAG_LABELS.get(k, k)}</span></label>'
        for k in FLAG_VALUES
    )

    flag_html = f"""
      <div>
        <div class="label">Flag</div>
        <details class="multiSelect">
          <summary>
            <span class="msValue">{flag_label}</span>
            <span class="msCaret">{ICON_CHEVRON_DOWN}</span>
          </summary>
          <div class="msPanel">
            {flag_checks}
          </div>
        </details>
      </div>
    """

    prof_val = _val(profesional_id)
    prof_options = "".join(
        f'<option value="{p.id}" {"selected" if str(p.id) == prof_val else ""}>{_profesional_label(p)}</option>'
        for p in (profesionales or [])
    )
    profesional_html = f"""
      <div>
        <div class="label">Profesional</div>
        <select name="profesional_id">
          <option value="">-</option>
          {prof_options}
        </select>
      </div>
    """

    back_link_html = ""
    if include_back_link:
        back_link_html = f'<a class="backLink" href="{back_href}"><span class="arrow">{ICON_ARROW_LEFT}</span><span>CRM</span></a>'

    return f"""
      <form method="get" action="{action}" style="margin-top:10px;" data-filter-form="1">
        {('<input type="hidden" name="open_filters" value="1"/>' if include_open_filters else '')}
        <div class="grid">
          <div>
            <div class="label">Buscar (cliente / tel / email / vehículo)</div>
            <input name="q" value="{_val(q)}" placeholder="Juan / +54... / mail@..."/>
          </div>
          {estado_html}
        </div>
        <div class="grid" style="margin-top:8px;">
          {flag_html}
          {profesional_html}
        </div>
        <div class="grid" style="margin-top:8px;">
          <div>
            <div class="label">Canal</div>
            <select name="canal">
              <option value="">-</option>
              {''.join(f'<option value="{c}" {"selected" if canal==c else ""}>{c}</option>' for c in CANAL_OPCIONES)}
            </select>
          </div>
          <div>
            <div class="label">Tipo vehículo</div>
            <select name="tipo_vehiculo">
              <option value="">-</option>
              {''.join(f'<option value="{t}" {"selected" if tipo_vehiculo==t else ""}>{t}</option>' for t in TIPOS_VEHICULO)}
            </select>
          </div>
        </div>
        <div class="grid" style="margin-top:8px;">
          <div>
            <div class="label">Marca</div>
            <input name="marca" value="{_val(marca)}"/>
          </div>
          <div>
            <div class="label">Modelo</div>
            <input name="modelo" value="{_val(modelo)}"/>
          </div>
        </div>
        <div class="grid" style="margin-top:8px;">
          <div>
            <div class="label">Año</div>
            <input name="anio" type="number" value="{_val(anio)}"/>
          </div>
          <div>
            <div class="label">Estado operativo</div>
            <select name="estado_revision">
              <option value="">-</option>
              {''.join(f'<option value="{s}" {"selected" if estado_revision==s else ""}>{s}</option>' for s in ESTADO_REVISION_OPCIONES)}
            </select>
          </div>
        </div>
        {zone_inputs_html}
        <div class="grid" style="margin-top:8px;">
          <div>
            <div class="label">Desde</div>
            <input type="date" name="from_date" value="{_val(from_date)}"/>
          </div>
          <div>
            <div class="label">Hasta</div>
            <input type="date" name="to_date" value="{_val(to_date)}"/>
          </div>
        </div>
        <div class="grid" style="margin-top:8px;">
          <div>
            <div class="label">Fecha por</div>
            <select name="date_field">
              <option value="turno" {"selected" if date_field=="turno" else ""}>Turno</option>
              <option value="created_at" {"selected" if date_field=="created_at" else ""}>Creaci-n de revisión</option>
            </select>
          </div>
        </div>
        <div class="grid" style="margin-top:8px;">
          <div>
            <div class="filterActionsRow">
              <button class="btn btn-primary" type="submit">Aplicar</button>
              <button class="btn" type="button" data-filter-save="1">Guardar</button>
              <button class="btn" type="button" data-filter-restore="1">Restaurar</button>
              <button class="btn" type="button" data-filter-clear="1" data-clear-href="{action}">Limpiar</button>
            </div>
            {back_link_html}
          </div>
        </div>
      </form>
    """


def _latest_revision(revs: list[Revision]) -> Revision | None:
    if not revs:
        return None
    return sorted(
        list(revs or []),
        key=lambda r: (r.created_at or datetime.min),
        reverse=True,
    )[0]


def _revision_approval_tag(rev: Revision) -> str | None:
    explicit = _clean_str_like(getattr(rev, "approval_tag", None))
    if explicit:
        return explicit
    estado = (_val(getattr(rev, "estado_revision", None)) or "").upper()
    if estado == "PENDIENTE" and getattr(rev, "turno_fecha", None):
        return "Esperando aprobación"
    return None


def _clean_str_like(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _revision_approval_status(rev: Revision) -> str | None:
    explicit = (_val(getattr(rev, "appointment_approval_status", None)) or "").upper()
    if explicit in {"PENDING", "APPROVED", "REJECTED"}:
        return explicit
    estado = (_val(getattr(rev, "estado_revision", None)) or "").upper()
    if estado == "PENDIENTE" and getattr(rev, "turno_fecha", None):
        return "PENDING"
    return None


def _revision_approval_tag(rev: Revision) -> str | None:
    status = _revision_approval_status(rev)
    if status == "PENDING":
        return "Esperando aprobaciÃ³n"
    if status == "APPROVED":
        return "Turno confirmado"
    if status == "REJECTED":
        return "Turno rechazado"
    explicit = _clean_str_like(getattr(rev, "approval_tag", None))
    if explicit:
        return explicit
    return None


def _render_revision_approval_ui(rev: Revision, include_actions: bool = False) -> str:
    status = _revision_approval_status(rev)
    if not status:
        return ""
    if status == "PENDING":
        label_html = '<span class="pill pill-approval-pending">AprobaciÃ³n turno: pendiente</span>'
        if include_actions:
            return (
                '<div class="revApprovalRow">'
                f"{label_html}"
                f'<button class="btn btn-sm btn-primary" type="button" onclick="updateAppointmentApproval({rev.id}, \'APPROVED\')">\u2713 Confirmar turno</button>'
                f'<button class="btn btn-sm btn-danger" type="button" onclick="updateAppointmentApproval({rev.id}, \'REJECTED\')">\u2717 Rechazar turno</button>'
                "</div>"
            )
        approval_tag = _revision_approval_tag(last_rev)
        if approval_tag:
            rev_lines.append(f"Aprobacion turno: {approval_tag}")
        return f'<div class="revApprovalRow">{label_html}</div>'
    if status == "APPROVED":
        return '<div class="revApprovalRow"><span class="pill pill-approval-approved">\u2713 Turno confirmado</span></div>'
    return '<div class="revApprovalRow"><span class="pill pill-approval-rejected">\u2717 Turno rechazado</span></div>'


def _revision_approval_tag(rev: Revision) -> str | None:
    status = _revision_approval_status(rev)
    if status == "PENDING":
        return "Esperando aprobacion"
    if status == "APPROVED":
        return "Turno confirmado"
    if status == "REJECTED":
        return "Turno rechazado"
    explicit = _clean_str_like(getattr(rev, "approval_tag", None))
    if explicit:
        return explicit
    return None


def _render_revision_approval_ui(rev: Revision, include_actions: bool = False, lead_id: int | None = None) -> str:
    status = _revision_approval_status(rev)
    if not status:
        return ""
    if status == "PENDING":
        if lead_id is not None:
            tf = getattr(rev, "turno_fecha", None)
            week_param = f"&week={(tf - timedelta(days=tf.weekday())).isoformat()}" if tf else ""
            label_html = f'<a class="pill pill-approval-pending" href="/calendar?highlight_lead_id={lead_id}{week_param}" style="cursor:pointer;text-decoration:none;">Aprobacion turno: pendiente</a>'
        else:
            label_html = '<span class="pill pill-approval-pending">Aprobacion turno: pendiente</span>'
        if include_actions:
            return (
                '<div class="revApprovalRow">'
                f"{label_html}"
                f'<button class="btn btn-sm btn-primary" type="button" onclick="updateAppointmentApproval({rev.id}, \'APPROVED\')">\u2713 Confirmar turno</button>'
                f'<button class="btn btn-sm btn-danger" type="button" onclick="updateAppointmentApproval({rev.id}, \'REJECTED\')">\u2717 Rechazar turno</button>'
                "</div>"
            )
        return f'<div class="revApprovalRow">{label_html}</div>'
    if status == "APPROVED":
        return '<div class="revApprovalRow"><span class="pill pill-approval-approved">\u2713 Turno confirmado</span></div>'
    return '<div class="revApprovalRow"><span class="pill pill-approval-rejected">\u2717 Turno rechazado</span></div>'


# ---------- constants ----------

KANBAN_ORDER = [
    "CONSULTA_NUEVA",
    "COORDINAR_DISPONIBILIDAD",
    "AGENDADO",
    "REVISION_COMPLETA",
]

KANBAN_COLUMN_WIDTH_PX = 322

KANBAN_LABELS = {
    "CONSULTA_NUEVA": "Consulta nueva",
    "COORDINAR_DISPONIBILIDAD": "Coordinar disponibilidad",
    "AGENDADO": "Agendado",
    "REVISION_COMPLETA": "Revisión completa",
}

FLAG_VALUES = [
    "PRESUPUESTANDO",
    "PRESUPUESTO_ENVIADO",
    "ACEPTADO",
    "RECOMPRA",
    "PERDIDO",
    "BUSCANDO_AUTO",
]

FLAG_LABELS = {
    "PRESUPUESTANDO": "Presupuestando",
    "PRESUPUESTO_ENVIADO": "Presupuesto enviado",
    "ACEPTADO": "Aceptado",
    "RECOMPRA": "Re-compra",
    "PERDIDO": "Perdido",
    "BUSCANDO_AUTO": "Buscando auto",
}

FLAG_FROM_ESTADO = {
    "CALIFICANDO": "PRESUPUESTANDO",
    "PRESUPUESTO_ENVIADO": "PRESUPUESTO_ENVIADO",
    "ACEPTADO": "ACEPTADO",
    "RECOMPRA": "RECOMPRA",
    "PERDIDO": "PERDIDO",
}

DEFAULT_OPER_ESTADO = "CONSULTA_NUEVA"

ESTADOS_VALIDOS = set(KANBAN_ORDER) | {"ATENCION_HUMANA"}
MOTIVOS_PERDIDA_VALIDOS = {"PRECIO", "DISPONIBILIDAD", "OTRO"}

PRECIO_BASE_BY_TIPO = {
    "AUTO": 120_000,
    "SUV_4X4_DEPORTIVO": 130_000,
    "CLASICO": 140_000,
    "ESCANEO_MOTOR": 80_000,
    "MOTO": 120_000,
}

MEDIOS_PAGO = ["EFECTIVO", "SANTANDER", "BRUBANK", "MERCADOPAGO", "UALA"]
VENDEDOR_TIPOS = ["PARTICULAR", "AGENCIA"]
REVISION_COMPRO_OPCIONES = ["SI", "NO", "OFRECIDO"]
TIPOS_VEHICULO = ["AUTO", "SUV_4X4_DEPORTIVO", "CLASICO", "ESCANEO_MOTOR", "MOTO"]

TIPO_VEHICULO_LABELS: dict[str, str] = {
    "AUTO": "Auto",
    "SUV_4X4_DEPORTIVO": "SUV",
    "CLASICO": "Clásico",
    "ESCANEO_MOTOR": "Escaneo",
    "MOTO": "Moto",
}


def _friendly_tipo_vehiculo(val: str | None) -> str:
    """Return the human-readable label for a tipo_vehiculo enum value."""
    if not val:
        return ""
    return TIPO_VEHICULO_LABELS.get(val, val.replace("_", " ").title())


CANAL_OPCIONES = [
    "IG_DM",
    "IG_WHATSAPP",
    "FB_DM",
    "FB_WHATSAPP",
    "WEBSITE",
    "GOOGLE",
    "GMAPS",
    "OTROS",
]

# Operational revision statuses (your request)
ESTADO_REVISION_OPCIONES = [
    "CONFIRMADO",
    "EN_PROCESO",
    "REPROGRAMAR",
    "COMPLETADO",
    "CANCELADO",
]


# ---------- small utils ----------

def next_estado(current: str) -> str | None:
    if current not in KANBAN_ORDER:
        return None
    i = KANBAN_ORDER.index(current)
    return KANBAN_ORDER[i + 1] if i + 1 < len(KANBAN_ORDER) else None

def prev_estado(current: str) -> str | None:
    if current not in KANBAN_ORDER:
        return None
    i = KANBAN_ORDER.index(current)
    return KANBAN_ORDER[i - 1] if i - 1 >= 0 else None

def _has(obj: Any, field: str) -> bool:
    return hasattr(obj, field)

def _get(obj: Any, field: str) -> Any:
    return getattr(obj, field, None)


def _lead_flag_value(lead: Lead) -> str | None:
    flag_val = _get(lead, "flag")
    if flag_val:
        return flag_val
    estado_val = _get(lead, "estado")
    return FLAG_FROM_ESTADO.get(estado_val)


def _lead_operational_estado(estado_val: str | None) -> str:
    if estado_val in KANBAN_ORDER:
        return estado_val
    if estado_val in FLAG_FROM_ESTADO:
        return DEFAULT_OPER_ESTADO
    return DEFAULT_OPER_ESTADO

def _lookup_viaticos(db: Session, zone_group: str | None, zone_detail: str | None) -> int | None:
    zg = (zone_group or "").strip()
    zd = (zone_detail or "").strip()
    if not zg:
        return None

    if zd:
        row = db.execute(
            select(ViaticosZone)
            .where(ViaticosZone.zone_group == zg)
            .where(ViaticosZone.zone_detail == zd)
        ).scalars().first()
        if row:
            return row.viaticos

    row = db.execute(
        select(ViaticosZone)
        .where(ViaticosZone.zone_group == zg)
        .where(ViaticosZone.zone_detail.is_(None))
    ).scalars().first()
    if row:
        return row.viaticos

    return None

def recalc_quote_if_possible(db: Session, rev: Revision) -> None:
    if rev.precio_base is None and rev.tipo_vehiculo:
        rev.precio_base = PRECIO_BASE_BY_TIPO.get(rev.tipo_vehiculo)

    if rev.viaticos is None:
        vv = _lookup_viaticos(db, rev.zone_group, rev.zone_detail)
        if vv is not None:
            rev.viaticos = vv

    if rev.precio_total is None and rev.precio_base is not None and rev.viaticos is not None:
        rev.precio_total = rev.precio_base + rev.viaticos


# ---------- rendering ----------

def render_page(
    leads: list[Lead],
    profesionales: list[Profesional] | None = None,
    agencias: list[Agencia] | None = None,
    user_email: str = "",
    q: str = "",
    estado: list[str] | None = None,
    flag: list[str] | None = None,
    profesional_id: str = "",
    canal: str = "",
    tipo_vehiculo: str = "",
    marca: str = "",
    modelo: str = "",
    anio: str = "",
    zone_group: str = "",
    zone_detail: str = "",
    estado_revision: str = "",
    turno_fecha_from: str = "",
    turno_fecha_to: str = "",
    zones_map: dict[str, list[str]] | None = None,
) -> str:
    css = _base_css()

    buckets: dict[str, list[Lead]] = {k: [] for k in KANBAN_ORDER}
    for l in leads:
        bucket_estado = _lead_operational_estado(_get(l, "estado"))
        buckets.setdefault(bucket_estado, []).append(l)

    html: list[str] = [css]
    html.append('<div class="layout">')

    # icons
    icon_board = '<svg class="icon" viewBox="0 0 24 24"><rect x="3" y="4" width="7" height="7"/><rect x="14" y="4" width="7" height="7"/><rect x="3" y="15" width="7" height="7"/><rect x="14" y="15" width="7" height="7"/></svg>'
    icon_calendar = '<svg class="icon" viewBox="0 0 24 24"><rect x="3" y="5" width="18" height="16" rx="2"/><path d="M8 3v4M16 3v4M3 11h18"/></svg>'
    icon_filter = '<svg class="icon" viewBox="0 0 24 24"><path d="M4 6h16l-6 7v5l-4 2v-7z"/></svg>'
    icon_prof = '<svg class="icon" viewBox="0 0 24 24"><circle cx="12" cy="7" r="4"/><path d="M4 21c1.5-4 14.5-4 16 0"/></svg>'
    icon_ag = '<svg class="icon" viewBox="0 0 24 24"><path d="M3 21h18"/><path d="M5 21V8l7-5 7 5v13"/><path d="M9 12h6"/></svg>'
    icon_toggle = '<svg class="icon icon-only" viewBox="0 0 24 24"><path d="M15 6l-6 6 6 6"/></svg>'
    build_stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    search_val = html_lib.escape(_val(q), quote=True)

    # LEFT SIDEBAR
    html.append("""
      <aside class="sidebar" id="sidebar">
        <div class="brandRow">
          <div class="brandText">RIDECHECK</div>
          <button class="sidebarToggle" type="button" onclick="toggleSidebar()" title="Collapse sidebar">%s</button>
        </div>
        %s
        %s
      </aside>
    """ % (
        icon_toggle,
        render_sidebar_nav(
            icon_board=icon_board,
            icon_calendar=icon_calendar,
            icon_filter=icon_filter,
            icon_prof=icon_prof,
            icon_ag=icon_ag,
            icon_wa=ICON_WHATSAPP,
        ),
        _sidebar_user_block(user_email),
    ))

    html.append('<main class="main">')
    html.append(f"""
      <div class="kanbanTopBar">
        <div class="kanbanTopBarTitle">CRM</div>
        <div class="kanbanTopBarRight">
          <span class="buildStamp">build: {build_stamp}</span>
          <div class="searchControl" id="kanban-search-control">
            <button class="iconBtn" id="kanban-search-toggle" type="button" title="Buscar (Ctrl+F)" aria-expanded="false">{ICON_SEARCH}</button>
            <div class="searchBoxWrap" id="kanban-search-wrap">
              <input id="kanban-search-input" class="searchInput" type="text" placeholder="Buscar leads..." value="{search_val}"/>
              <span id="kanban-search-count" class="searchCount">0 / 0</span>
              <button class="iconBtn" id="kanban-search-close" type="button" title="Cerrar búsqueda">{ICON_CLOSE}</button>
            </div>
          </div>
        </div>
      </div>
    """)

    create_lead_form_html = """
        <form method="post" action="/ui/lead_create" data-add-lead-form="1" style="margin-top:10px;">
          <div class="grid">
            <div>
              <div class="label">Nombre</div>
              <input name="nombre" placeholder="Nombre"/>
            </div>
            <div>
              <div class="label">Apellido</div>
              <input name="apellido" placeholder="Apellido"/>
            </div>
          </div>

          <div class="grid" style="margin-top:8px;">
            <div>
              <div class="label">Teléfono</div>
              <input name="telefono" placeholder="+54..."/>
            </div>
            <div>
              <div class="label">Email</div>
              <input name="email" placeholder="mail@..."/>
            </div>
          </div>

          <div class="grid" style="margin-top:8px;">
            <div>
              <div class="label">Canal</div>
              <select name="canal">
                <option value="">-</option>
                %s
              </select>
            </div>
            <div>
              <div class="label">Compró el auto</div>
              <select name="compro_el_auto">
                <option value="">-</option>
                <option value="SI">SI</option>
                <option value="NO">NO</option>
              </select>
            </div>
          </div>

          <div class="stack" style="margin-top:10px;">
            <button class="btn btn-primary" type="submit" data-add-lead-submit="1">Create lead</button>
            <button class="btn" type="button" onclick="closeAddLeadPopover()">Cancelar</button>
          </div>
          <div class="addLeadError" data-add-lead-error="1" aria-live="polite"></div>
        </form>
    """ % "".join([f'<option value="{c}">{c}</option>' for c in CANAL_OPCIONES])
    _mobile_tab_items = "".join(
        f'<button type="button" class="kanban-tab" data-col-index="{i}" data-col-key="{k}">'
        f'<span>{KANBAN_LABELS.get(k, k)}</span>'
        f'<span class="badge">{len(buckets.get(k, []))}</span>'
        f'</button>'
        for i, k in enumerate(KANBAN_ORDER)
    )
    html.append(
        f'<nav class="kanban-mobile-tabs" aria-label="Columnas kanban">'
        f'<div class="kanban-mobile-tabs-inner">{_mobile_tab_items}</div>'
        f'</nav>'
    )
    html.append('<div class="board">')

    for estado_k in KANBAN_ORDER:
        col = buckets.get(estado_k, [])
        if estado_k == "CONSULTA_NUEVA":
            header_actions = f"""
              <details class="menu">
                <summary class="btn btn-sm addLeadSummary">{ICON_PLUS_THIN}<span>Lead</span></summary>
                <div class="menuPanel" id="add-lead-panel" data-menu-kind="add-lead">
                  <div class="menuTitle">Crear lead</div>
                  {create_lead_form_html}
                </div>
              </details>
            """
        else:
            header_actions = ""

        estado_label = KANBAN_LABELS.get(estado_k, estado_k)
        html.append(
            f'<div id="kanban-col-{estado_k}" class="kanban-column kanbanCol" data-estado="{estado_k}" data-estado-label="{estado_label}" data-card-count="{len(col)}"><h2 class="kanban-column-header"><span>{estado_label}</span> '
            f'<span class="badge">{len(col)}</span> {header_actions}</h2>'
            f'<div class="kanban-column-cards">'
        )
        for l in col:
            html.append(render_lead_card(l, zones_map, profesionales=profesionales or [], agencias=agencias or []))
        html.append("</div></div>")

    html.append("</div>")  # board
    html.append('<div id="popover-root"></div>')
    html.append('<button id="mobile-add-lead-btn" onclick="window._mobileAddLead()" style="display:none;position:fixed;bottom:24px;right:24px;width:56px;height:56px;border-radius:50%;background:#111827;color:white;font-size:24px;line-height:1;display:flex;align-items:center;justify-content:center;border:none;z-index:1700;box-shadow:0 4px 12px rgba(0,0,0,.3);cursor:pointer;">+</button>')
    html.append('<div id="toast-root" class="toastWrap"></div>')
    zones_json = json.dumps(zones_map or {}, ensure_ascii=False).replace("</", "<\\/")

    html.append(f"""
      <script type="application/json" id="zones-data">{zones_json}</script>
    """)

    html.append("""
      <script>
        (function () {
          var zonesEl = document.getElementById("zones-data");
          var zonesMap = {};
          if (zonesEl && zonesEl.textContent) {
            try {
              zonesMap = JSON.parse(zonesEl.textContent);
            } catch (e) {
              zonesMap = {};
            }
          }
          var searchControl = document.getElementById("kanban-search-control");
          var searchWrap = document.getElementById("kanban-search-wrap");
          var searchInput = document.getElementById("kanban-search-input");
          var searchToggleBtn = document.getElementById("kanban-search-toggle");
          var searchCloseBtn = document.getElementById("kanban-search-close");
          var searchCount = document.getElementById("kanban-search-count");

          function normalizeSearchText(value) {
            return (value || "")
              .toString()
              .normalize("NFD")
              .replace(/[\\u0300-\\u036f]/g, "")
              .toLowerCase()
              .trim();
          }

          function applyKanbanSearch() {
            var q = normalizeSearchText(searchInput ? searchInput.value : "");
            var cards = document.querySelectorAll(".leadCard");
            var total = 0;
            var visible = 0;
            cards.forEach(function (card) {
              total += 1;
              var haystack = normalizeSearchText(card.getAttribute("data-search") || "");
              var show = !q || haystack.indexOf(q) !== -1;
              card.classList.toggle("search-hidden", !show);
              if (show) visible += 1;
            });
            document.querySelectorAll(".kanbanCol").forEach(function (col) {
              var badge = col.querySelector("h2 .badge");
              if (badge) badge.textContent = String(col.querySelectorAll(".leadCard:not(.search-hidden)").length);
            });
            if (searchCount) {
              searchCount.textContent = q ? (visible + " / " + total) : (total + " / " + total);
            }
          }

          function openKanbanSearch(focusInput) {
            if (!searchControl || !searchWrap || !searchToggleBtn) return;
            searchControl.classList.add("open");
            searchToggleBtn.setAttribute("aria-expanded", "true");
            if (focusInput && searchInput) {
              searchInput.focus();
              searchInput.select();
            }
            applyKanbanSearch();
          }

          function closeKanbanSearch(clearValue) {
            if (!searchControl || !searchWrap || !searchToggleBtn) return;
            if (clearValue && searchInput) {
              searchInput.value = "";
            }
            searchControl.classList.remove("open");
            searchToggleBtn.setAttribute("aria-expanded", "false");
            applyKanbanSearch();
          }

          if (searchToggleBtn) {
            searchToggleBtn.addEventListener("click", function () {
              var isOpen = searchControl && searchControl.classList.contains("open");
              if (isOpen) {
                closeKanbanSearch(false);
                return;
              }
              openKanbanSearch(true);
            });
          }
          if (searchCloseBtn) {
            searchCloseBtn.addEventListener("click", function () {
              closeKanbanSearch(true);
            });
          }
          if (searchInput) {
            searchInput.addEventListener("input", applyKanbanSearch);
          }

          function refreshZoneDetails(scope) {
            if (!zonesMap || Object.keys(zonesMap).length === 0) return;
            var groupSel = scope.querySelector('select[data-zone-group]');
            var detailSel = scope.querySelector('select[data-zone-detail]');
            if (!groupSel || !detailSel) return;
            var groupVal = groupSel.value || "";
            var options = zonesMap[groupVal] || [];
            var current = detailSel.value || "";
            detailSel.innerHTML = '<option value="">-</option>';
            options.forEach(function (d) {
              var opt = document.createElement("option");
              opt.value = d;
              opt.textContent = d;
              if (d === current) opt.selected = true;
              detailSel.appendChild(opt);
            });
          }

          document.addEventListener("change", function (e) {
            var el = e.target;
            if (el && el.matches('select[data-zone-group]')) {
              var form = el.closest("form") || document;
              refreshZoneDetails(form);
            }
          });

          window.addEventListener("DOMContentLoaded", function () {
            if (!zonesMap || Object.keys(zonesMap).length === 0) return;
            document.querySelectorAll("form").forEach(function (f) {
              refreshZoneDetails(f);
            });
          });

          window.openRevisionArea = function (leadId) {
            if (!leadId) return;
            var revs = document.getElementById("revs-" + leadId);
            if (revs) revs.open = true;
          };

          function setSidebarCollapsed(collapsed) {
            var sb = document.getElementById("sidebar");
            if (!sb) return;
            sb.classList.toggle("collapsed", collapsed);
            localStorage.setItem("sidebar_collapsed", collapsed ? "1" : "0");
          }

          window.toggleSidebar = function () {
            var sb = document.getElementById("sidebar");
            if (!sb) return;
            var collapsed = sb.classList.contains("collapsed");
            setSidebarCollapsed(!collapsed);
          };

          window.openEditLatest = function (leadId) {
            if (!leadId) return;
            closeAllOpenPopovers();
            document.querySelectorAll("details.menu[open]").forEach(function (menu) {
              menu.open = false;
            });
            var revs = document.getElementById("revs-" + leadId);
            if (revs) revs.open = true;
            var edit = document.getElementById("editrev-" + leadId);
            if (edit) {
              closeOpenRevisionEditors(leadId);
              edit.classList.add("open");
              document.body.classList.add("modal-open");
              syncLeadDetailsHeights();
              var firstInput = edit.querySelector("input, select, textarea");
              if (firstInput) firstInput.focus();
            }
          };
          window.closeEditLatest = function (leadId) {
            var edit = document.getElementById("editrev-" + leadId);
            if (!edit) return;
            edit.classList.remove("open");
            if (!document.querySelector(".revModalOverlay.open")) {
              document.body.classList.remove("modal-open");
            }
          };
          window._mobileAddLead = function() { var s = document.getElementById("kanban-col-CONSULTA_NUEVA"); if(s) { var btn = s.querySelector(".addLeadSummary"); if(btn) btn.click(); } };
          window.closeAddLeadPopover = function () {
            closeAllOpenPopovers();
          };

          function ensureInViewport(el, opts) {
            if (!el) return;
            var options = opts || {};
            var padding = typeof options.padding === "number" ? options.padding : 16;
            var center = options.center === true;
            var rect = el.getBoundingClientRect();
            var viewH = window.innerHeight || 0;
            if (!viewH) return;
            var topLimit = padding;
            var bottomLimit = viewH - padding;
            var delta = 0;
            if (center) {
              var rectCenter = rect.top + rect.height / 2;
              var viewCenter = viewH / 2;
              delta = rectCenter - viewCenter;
            } else {
              if (rect.top < topLimit) {
                delta = rect.top - topLimit;
              } else if (rect.bottom > bottomLimit) {
                delta = rect.bottom - bottomLimit;
              }
            }
            if (Math.abs(delta) > 1) {
              window.scrollBy({ top: delta, behavior: "smooth" });
            }
          }
          window.ensureInViewport = ensureInViewport;

          function getScrollContainer(el) {
            var node = el ? el.parentElement : null;
            while (node && node !== document.body) {
              var style = window.getComputedStyle(node);
              var oy = style.overflowY;
              if ((oy === "auto" || oy === "scroll") && node.scrollHeight > node.clientHeight) {
                return node;
              }
              node = node.parentElement;
            }
            return null;
          }

          function centerInScrollContainer(el) {
            if (!el) return;
            var sc = getScrollContainer(el);
            if (!sc) return;
            var er = el.getBoundingClientRect();
            var sr = sc.getBoundingClientRect();
            var delta = (er.top + er.height / 2) - (sr.top + sr.height / 2);
            sc.scrollTo({ top: sc.scrollTop + delta, behavior: "smooth" });
          }

          function scrollCardIntoViewFrom(el) {
            if (!el) return;
            var card = el.closest(".card");
            if (card) {
              ensureInViewport(card, { padding: 16, center: true });
            }
          }
          window.scrollCardIntoViewFrom = scrollCardIntoViewFrom;

          window.openPerdidoInline = function (leadId, el) {
            if (!leadId) return;
            var panel = document.getElementById("perdido-inline-" + leadId);
            if (!panel) return;
            var menu = panel.closest("details.menu");
            var trigger = menu ? menu.querySelector("summary") : null;
            var isInsideActive = activePanel && activePanel.contains(panel);
            if (!isInsideActive) {
              closeAllOpenPopovers();
              if (menu && trigger) openMenu(menu, trigger);
            }
            var main = document.getElementById("menu-main-" + leadId);
            if (activePanel) activePanel.querySelectorAll(".menuInline").forEach(function (node) { node.style.display = "none"; });
            if (main) main.classList.add("hidden");
            panel.style.display = "block";
            centerInScrollContainer(el || panel);
            ensureInViewport(panel, { center: true, padding: 20 });
            if (activeTrigger && activePanel) positionMenuPanel(activeTrigger, activePanel);
          };

          window.closePerdidoInline = function (leadId) {
            var panel = document.getElementById("perdido-inline-" + leadId);
            var main = document.getElementById("menu-main-" + leadId);
            if (panel) panel.style.display = "none";
            if (main) main.classList.remove("hidden");
          };

          window.openLeadEditModal = function (leadId, el) {
            if (!leadId) return;
            var panel = document.getElementById("editlead-" + leadId);
            if (!panel) return;
            closeAllOpenPopovers(leadId);
            panel.classList.add("open");
            document.body.classList.add("modal-open");
            var firstInput = panel.querySelector("input, select, textarea");
            if (firstInput) firstInput.focus();
          };

          window.closeLeadEditModal = function (leadId) {
            var panel = document.getElementById("editlead-" + leadId);
            if (!panel) return;
            panel.classList.remove("open");
            if (!document.querySelector(".revModalOverlay.open")) {
              document.body.classList.remove("modal-open");
            }
          };

          window.openLeadEditInline = function (leadId, el) {
            openLeadEditModal(leadId, el);
          };

          window.closeLeadEditInline = function (leadId) {
            closeLeadEditModal(leadId);
          };

          window.toggleLeadDetails = function (leadId, btn) {
            var body = document.getElementById("lead-details-" + leadId);
            if (!body || !btn) return;
            var opened = body.classList.toggle("open");
            if (opened) {
              body.style.setProperty("--lead-details-max", body.scrollHeight + "px");
            }
            btn.setAttribute("aria-expanded", opened ? "true" : "false");
          };

          function syncLeadDetailsHeights() {
            document.querySelectorAll(".leadDetailsBody.open").forEach(function (body) {
              body.style.setProperty("--lead-details-max", body.scrollHeight + "px");
            });
          }

          document.addEventListener("submit", function (e) {
            var form = e.target;
            if (form && form.matches('form[data-add-lead-form]')) {
              e.preventDefault();
              var submitBtn = form.querySelector('[data-add-lead-submit]');
              var errorEl = form.querySelector('[data-add-lead-error]');
              if (errorEl) errorEl.textContent = "";
              if (submitBtn) submitBtn.disabled = true;
              var body = new URLSearchParams();
              new FormData(form).forEach(function (value, key) {
                body.append(key, String(value));
              });
              fetch(form.getAttribute("action") || "/ui/lead_create", {
                method: "POST",
                headers: { "Content-Type": "application/x-www-form-urlencoded" },
                body: body.toString(),
                redirect: "follow",
              }).then(function (res) {
                if (!res.ok) throw new Error("lead_create_failed");
                closeAllOpenPopovers();
                window.location.href = res.url || "/kanban";
              }).catch(function () {
                if (errorEl) {
                  errorEl.textContent = "No se pudo crear el lead. Revis- los datos e intent- de nuevo.";
                }
              }).finally(function () {
                if (submitBtn) submitBtn.disabled = false;
              });
              return;
            }
            if (form && form.matches('form[data-rev-create]')) {
              var input = form.querySelector('input[name="lead_id"]');
              if (input) {
                localStorage.setItem("open_revs_lead", input.value);
                localStorage.setItem("open_edit_latest_lead", input.value);
              }
            }
            if (form && form.matches('form[action="/ui/perdido"]')) {
              var inline = form.closest(".menuInline");
              if (inline) inline.style.display = "none";
              closeAllOpenPopovers();
            }
            if (form && form.matches('form[action="/ui/lead_update"]')) {
              closeAllOpenPopovers();
              var leadInput = form.querySelector('input[name="lead_id"]');
              if (leadInput && leadInput.value) {
                window.location.hash = "lead-" + leadInput.value;
              }
            }
            if (form && form.matches('form[action="/ui/revision_latest_update"]')) {
              if (e.defaultPrevented) {
                return;
              }
              closeAllOpenPopovers();
            }
          });

          var activeMenu = null;
          var activeTrigger = null;
          var activePanel = null;
          var menuPanelHomes = new WeakMap();

          function positionMenuPanel(triggerEl, panel) {
            if (!triggerEl || !panel) return;
            var rect = triggerEl.getBoundingClientRect();
            panel.style.position = "fixed";
            panel.style.left = "0px";
            panel.style.top = "0px";
            panel.style.right = "auto";
            panel.style.bottom = "auto";
            panel.style.zIndex = "1800";
            panel.style.visibility = "hidden";
            panel.style.display = "block";
            var pad = 8;
            var width = panel.offsetWidth;
            var height = panel.offsetHeight;
            var left = rect.right - width;
            var top = rect.bottom + 6;
            if (left < pad) left = pad;
            if (left + width > window.innerWidth - pad) {
              left = Math.max(pad, window.innerWidth - width - pad);
            }
            if (top + height > window.innerHeight - pad) {
              top = rect.top - height - 6;
            }
            if (top < pad) top = pad;
            panel.style.left = left + "px";
            panel.style.top = top + "px";
            panel.style.visibility = "visible";
          }

          function restoreMenuPanel(detailsEl, panel) {
            if (!detailsEl || !panel) return;
            var home = menuPanelHomes.get(detailsEl);
            if (home && home.parent) {
              if (home.nextSibling && home.nextSibling.parentNode === home.parent) {
                home.parent.insertBefore(panel, home.nextSibling);
              } else {
                home.parent.appendChild(panel);
              }
            } else {
              detailsEl.appendChild(panel);
            }
            panel.classList.remove("portal");
            panel.style.position = "";
            panel.style.left = "";
            panel.style.top = "";
            panel.style.right = "";
            panel.style.bottom = "";
            panel.style.zIndex = "";
            panel.style.visibility = "";
            panel.style.display = "";
          }

          function closeActiveMenu() {
            if (!activePanel) return;
            activePanel.querySelectorAll(".menuInline").forEach(function (el) {
              el.style.display = "none";
            });
            activePanel.querySelectorAll(".menuMainActions.hidden").forEach(function (el) {
              el.classList.remove("hidden");
            });
            if (activeMenu) {
              restoreMenuPanel(activeMenu, activePanel);
              activeMenu.removeAttribute("data-menu-open");
              activeMenu.removeAttribute("data-portalized");
              activeMenu.open = false;
            }
            activeMenu = null;
            activeTrigger = null;
            activePanel = null;
          }
          function closeOpenRevisionEditors(exceptLeadId) {
            document.querySelectorAll(".revModalOverlay.open").forEach(function (d) {
              if (exceptLeadId && (d.id === "editrev-" + exceptLeadId || d.id === "editlead-" + exceptLeadId)) return;
              d.classList.remove("open");
            });
            if (!document.querySelector(".revModalOverlay.open")) {
              document.body.classList.remove("modal-open");
            }
          }

          function closeAllOpenPopovers(exceptLeadId) {
            closeActiveMenu();
            document.querySelectorAll("details.menu[open]").forEach(function (menu) {
              menu.open = false;
              menu.removeAttribute("data-menu-open");
              menu.removeAttribute("data-portalized");
            });
            closeOpenRevisionEditors(exceptLeadId);
          }
          window.closeAllPopovers = closeAllOpenPopovers;

          function handleOutsideClick(e) {
            var root = document.getElementById("popover-root");
            if (activePanel) {
              if (activePanel.contains(e.target)) return;
              if (activeTrigger && activeTrigger.contains(e.target)) return;
            }
            if (root && root.contains(e.target)) return;
            if (e.target && e.target.closest && e.target.closest("details.menu")) return;
            var insideRevEdit = e.target && e.target.closest && e.target.closest(".revModal");
            if (insideRevEdit) return;
            closeAllOpenPopovers();
          }

          function handleEsc(e) {
            if (e.key !== "Escape") return;
            if (searchControl && searchControl.classList.contains("open")) {
              closeKanbanSearch(true);
              return;
            }
            closeAllOpenPopovers();
          }

          function openMenu(detailsEl, triggerEl) {
            if (!detailsEl || !triggerEl) return;
            if (document.querySelector(".revModalOverlay.open")) {
              closeAllOpenPopovers();
              return;
            }
            if (activeMenu === detailsEl) {
              closeAllOpenPopovers();
              return;
            }
            closeAllOpenPopovers();
            var panel = detailsEl.querySelector(".menuPanel");
            var root = document.getElementById("popover-root");
            if (!panel || !root) return;
            activeMenu = detailsEl;
            activeTrigger = triggerEl;
            activePanel = panel;
            if (!menuPanelHomes.has(detailsEl)) {
              menuPanelHomes.set(detailsEl, {
                parent: panel.parentNode,
                nextSibling: panel.nextSibling,
              });
            }
            detailsEl.open = true;
            detailsEl.setAttribute("data-menu-open", "1");
            detailsEl.setAttribute("data-portalized", "1");
            panel.classList.add("portal");
            root.appendChild(panel);
            centerInScrollContainer(triggerEl);
            ensureInViewport(triggerEl, { center: true, padding: 24 });
            positionMenuPanel(triggerEl, panel);
            ensureInViewport(panel, { padding: 12 });
          }

          function openAddLeadPopover(e) {
            if (e) { e.stopPropagation(); e.preventDefault(); }
            var summary = document.querySelector('#kanban-col-CONSULTA_NUEVA .addLeadSummary');
            if (!summary) return;
            summary.click();
          }

          function scrollBoardToEstado(estado) {
            if (!estado) return;
            var board = document.querySelector(".board");
            if (!board) return;
            var col = board.querySelector('.kanban-column[data-estado="' + estado + '"]');
            if (!col) return;
            var left = col.offsetLeft;
            var right = left + col.offsetWidth;
            var viewLeft = board.scrollLeft;
            var viewRight = viewLeft + board.clientWidth;
            if (left < viewLeft) {
              board.scrollTo({ left: left, behavior: "smooth" });
            } else if (right > viewRight) {
              board.scrollTo({ left: right - board.clientWidth, behavior: "smooth" });
            }
          }

          function postForm(url, data) {
            var body = new URLSearchParams();
            Object.keys(data || {}).forEach(function (k) {
              body.append(k, String(data[k]));
            });
            return fetch(url, {
              method: "POST",
              headers: { "Content-Type": "application/x-www-form-urlencoded" },
              body: body.toString(),
            });
          }

          function updateAppointmentApproval(revisionId, status) {
            if (!revisionId || !status) return;
            return fetch("/api/revisions/" + revisionId + "/appointment-approval", {
              method: "PATCH",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ status: status }),
            }).then(function (resp) {
              if (!resp.ok) throw new Error("No se pudo actualizar la aprobacion del turno");
              window.location.reload();
            }).catch(function (err) {
              window.alert(err && err.message ? err.message : "No se pudo actualizar la aprobacion del turno");
            });
          }
          window.updateAppointmentApproval = updateAppointmentApproval;

          function updateColumnCounts() {
            document.querySelectorAll(".kanbanCol").forEach(function (col) {
              var badge = col.querySelector("h2 .badge");
              if (badge) badge.textContent = String(col.querySelectorAll(".leadCard:not(.search-hidden)").length);
            });
          }

          function updateRevCount(leadId, count) {
            var span = document.getElementById("rev-count-" + leadId);
            if (span) {
              span.setAttribute("data-rev-count", String(count));
              span.textContent = "Ver revisiones (" + count + ")";
            }
            var card = document.getElementById("lead-" + leadId);
            if (!card) return;
            var pill = card.querySelector(".pill-count");
            if (pill) pill.textContent = "Revs: " + count;
          }

          function showUndoToast(message, seconds, onUndo, onExpire) {
            var root = document.getElementById("toast-root");
            if (!root) return;
            if (window.__undoToastTimer) {
              window.clearInterval(window.__undoToastTimer);
              window.__undoToastTimer = null;
            }
            root.innerHTML = "";
            var left = seconds;
            var toast = document.createElement("div");
            toast.className = "toast";
            toast.innerHTML = '<span>' + message + '</span><button type="button">Deshacer</button><span>(' + left + ')</span>';
            root.appendChild(toast);
            var undoBtn = toast.querySelector("button");
            var counter = toast.querySelector("span:last-child");
            var done = false;
            window.__undoToastTimer = window.setInterval(function () {
              left -= 1;
              if (counter) counter.textContent = "(" + left + ")";
              if (left <= 0) {
                window.clearInterval(window.__undoToastTimer);
                window.__undoToastTimer = null;
                if (!done && onExpire) onExpire();
                root.innerHTML = "";
              }
            }, 1000);
            undoBtn.addEventListener("click", function () {
              if (done) return;
              done = true;
              window.clearInterval(window.__undoToastTimer);
              window.__undoToastTimer = null;
              root.innerHTML = "";
              if (onUndo) onUndo();
            });
          }

          function showErrorToast(message, seconds) {
            var root = document.getElementById("toast-root");
            if (!root) return;
            if (window.__undoToastTimer) {
              window.clearInterval(window.__undoToastTimer);
              window.__undoToastTimer = null;
            }
            root.innerHTML = "";
            var toast = document.createElement("div");
            toast.className = "toast";
            toast.innerHTML = '<span>' + message + '</span>';
            root.appendChild(toast);
            window.setTimeout(function () {
              if (root.contains(toast)) root.removeChild(toast);
            }, Math.max(1200, (seconds || 2) * 1000));
          }

          window.requestDeleteLead = async function (leadId) {
            var card = document.getElementById("lead-" + leadId);
            if (!card) return;
            var parent = card.parentElement;
            var next = card.nextElementSibling;
            var req = await postForm("/ui/request_delete_lead", { lead_id: leadId });
            if (!req.ok) return;
            var payload = await req.json();
            closeAllOpenPopovers();
            card.remove();
            updateColumnCounts();
            showUndoToast("Eliminado.", payload.countdown_seconds || 7, async function () {
              await postForm("/ui/undo_delete", { token: payload.token });
              if (next && next.parentElement === parent) parent.insertBefore(card, next);
              else parent.appendChild(card);
              updateColumnCounts();
            }, async function () {
              var commit = await postForm("/ui/commit_delete", { token: payload.token });
              if (!commit.ok) {
                if (next && next.parentElement === parent) parent.insertBefore(card, next);
                else parent.appendChild(card);
                updateColumnCounts();
              }
            });
          };

          window.requestDeleteLatestRevision = async function (leadId) {
            var revWrap = document.getElementById("revs-" + leadId);
            if (!revWrap) return;
            var span = document.getElementById("rev-count-" + leadId);
            var count = span ? parseInt(span.getAttribute("data-rev-count") || "0", 10) : 0;
            var req = await postForm("/ui/request_delete_revision", { lead_id: leadId });
            if (!req.ok) return;
            var payload = await req.json();
            closeAllOpenPopovers();
            var revEl = document.getElementById("rev-" + leadId + "-" + payload.revision_id) || revWrap.querySelector(".rev");
            if (!revEl) return;
            var parent = revEl.parentElement;
            var next = revEl.nextElementSibling;
            revEl.remove();
            updateRevCount(leadId, Math.max(0, count - 1));
            showUndoToast("Eliminado.", payload.countdown_seconds || 7, async function () {
              await postForm("/ui/undo_delete", { token: payload.token });
              if (next && next.parentElement === parent) parent.insertBefore(revEl, next);
              else parent.appendChild(revEl);
              updateRevCount(leadId, count);
            }, async function () {
              var commit = await postForm("/ui/commit_delete", { token: payload.token });
              if (!commit.ok) {
                if (next && next.parentElement === parent) parent.insertBefore(revEl, next);
                else parent.appendChild(revEl);
                updateRevCount(leadId, count);
              }
            });
          };

          document.addEventListener("click", function (e) {
            var summary = e.target.closest("details.menu > summary");
            if (!summary) return;
            if (draggingCard) return;
            e.preventDefault();
            openMenu(summary.parentElement, summary);
          });
          var addLeadPanel = document.getElementById("add-lead-panel");
          if (addLeadPanel) {
            ["mousedown", "click"].forEach(function (evt) {
              addLeadPanel.addEventListener(evt, function (e) {
                e.stopPropagation();
              });
            });
          }
          document.addEventListener("mousedown", handleOutsideClick, true);
          document.addEventListener("keydown", handleEsc, true);
          document.addEventListener("keydown", function (e) {
            if (!(e.ctrlKey || e.metaKey)) return;
            if ((e.key || "").toLowerCase() !== "f") return;
            e.preventDefault();
            openKanbanSearch(true);
          }, true);
          window.addEventListener("resize", function () {
            if (activePanel && activeTrigger) positionMenuPanel(activeTrigger, activePanel);
            syncLeadDetailsHeights();
          });
          document.addEventListener("scroll", function () {
            if (activePanel && activeTrigger) positionMenuPanel(activeTrigger, activePanel);
          }, true);

          function clearDragHighlights() {
            document.querySelectorAll(".kanbanCol.drag-over").forEach(function (c) {
              c.classList.remove("drag-over");
            });
          }

          async function moveLead(leadId, estado) {
            var res = await postForm("/ui/move_lead", { lead_id: leadId, new_estado: estado });
            return res.ok;
          }
          function findKanbanColumnByEstado(estado) {
            return document.querySelector('.kanban-column[data-estado="' + estado + '"]');
          }

          function syncCardEstadoUI(card, estado) {
            if (!card || !estado) return;
            card.setAttribute("data-current-estado", estado);
            var col = findKanbanColumnByEstado(estado);
            var statusEl = card.querySelector(".leadStatus");
            if (col && statusEl && statusEl.getAttribute("data-status-locked") !== "1") {
              var label = col.getAttribute("data-estado-label") || estado;
              statusEl.textContent = label;
            }
            card.querySelectorAll('select[data-quick-estado="1"]').forEach(function (sel) {
              sel.value = estado;
            });
          }

          var draggingCard = null;
          var dragOriginCol = null;
          var dragOriginNext = null;
          var placeholder = document.createElement("div");
          placeholder.className = "dropPlaceholder";

          function insertPlaceholder(col, y) {
            var cards = Array.prototype.slice.call(col.querySelectorAll(".leadCard:not(.dragging)"));
            var before = null;
            for (var i = 0; i < cards.length; i += 1) {
              var rect = cards[i].getBoundingClientRect();
              if (y < rect.top + rect.height / 2) {
                before = cards[i];
                break;
              }
            }
            if (before) col.insertBefore(placeholder, before);
            else col.appendChild(placeholder);
          }

          document.addEventListener("dragstart", function (e) {
            var handle = e.target.closest('[data-drag-handle="1"]');
            if (!handle) return;
            var card = handle.closest(".leadCard");
            if (!card) return;
            closeAllOpenPopovers();
            draggingCard = card;
            dragOriginCol = card.parentElement;
            dragOriginNext = card.nextElementSibling;
            e.dataTransfer.setData("text/plain", card.getAttribute("data-lead-id"));
            e.dataTransfer.effectAllowed = "move";
            card.classList.add("dragging");
          });

          document.addEventListener("dragend", function () {
            if (draggingCard) draggingCard.classList.remove("dragging");
            draggingCard = null;
            dragOriginCol = null;
            dragOriginNext = null;
            if (placeholder.parentElement) placeholder.parentElement.removeChild(placeholder);
            clearDragHighlights();
          });

          document.querySelectorAll(".kanbanCol").forEach(function (col) {
            col.addEventListener("dragover", function (e) {
              if (!draggingCard) return;
              e.preventDefault();
              col.classList.add("drag-over");
              insertPlaceholder(col, e.clientY);
              centerInScrollContainer(col);
            });
            col.addEventListener("dragleave", function (e) {
              if (!col.contains(e.relatedTarget)) col.classList.remove("drag-over");
            });
            col.addEventListener("drop", async function (e) {
              e.preventDefault();
              if (!draggingCard) return;
              clearDragHighlights();
              var leadId = draggingCard.getAttribute("data-lead-id");
              if (!leadId) return;
              var targetEstado = col.getAttribute("data-estado");
              var currentEstado = draggingCard.getAttribute("data-current-estado");
              if (placeholder.parentElement === col) col.insertBefore(draggingCard, placeholder);
              if (placeholder.parentElement) placeholder.parentElement.removeChild(placeholder);
              if (targetEstado === currentEstado) return;
              syncCardEstadoUI(draggingCard, targetEstado);
              updateColumnCounts();
              var ok = await moveLead(leadId, targetEstado);
              if (!ok) {
                if (dragOriginCol) {
                  if (dragOriginNext && dragOriginNext.parentElement === dragOriginCol) {
                    dragOriginCol.insertBefore(draggingCard, dragOriginNext);
                  } else {
                    dragOriginCol.appendChild(draggingCard);
                  }
                }
                syncCardEstadoUI(draggingCard, currentEstado);
                updateColumnCounts();
                showErrorToast("No se pudo mover la tarjeta.", 2);
              }
            });
          });

          document.addEventListener("change", async function (e) {
            var select = e.target;
            if (!select || !select.matches('select[data-quick-estado="1"]')) return;
            var leadId = select.getAttribute("data-lead-id");
            if (!leadId) return;
            var card = document.getElementById("lead-" + leadId);
            if (!card) return;
            var targetEstado = select.value || "";
            var currentEstado = card.getAttribute("data-current-estado") || "";
            if (!targetEstado || targetEstado === currentEstado) return;
            var targetCol = findKanbanColumnByEstado(targetEstado);
            if (!targetCol) {
              select.value = currentEstado;
              return;
            }

            var originCol = card.parentElement;
            var originNext = card.nextElementSibling;
            closeAllOpenPopovers();
            targetCol.appendChild(card);
            syncCardEstadoUI(card, targetEstado);
            updateColumnCounts();
            scrollBoardToEstado(targetEstado);
            select.disabled = true;
            var ok = await moveLead(leadId, targetEstado);
            select.disabled = false;
            if (ok) return;

            if (originCol) {
              if (originNext && originNext.parentElement === originCol) {
                originCol.insertBefore(card, originNext);
              } else {
                originCol.appendChild(card);
              }
            }
            syncCardEstadoUI(card, currentEstado);
            updateColumnCounts();
            showErrorToast("No se pudo mover la tarjeta.", 2);
          });

          function highlightLeadCard(leadId) {
            if (!leadId) return;
            var el = document.getElementById("lead-" + leadId);
            if (!el) return;
            el.scrollIntoView({ behavior: "smooth", block: "center" });
            el.classList.add("flash");
            setTimeout(function () { el.classList.remove("flash"); }, 2000);
          }

          function waNorm(v) {
            return (v || "").toString().normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase().trim();
          }

          async function fetchLeadThreadInfo(leadId) {
            var resp = await fetch("/leads/" + leadId + "/whatsapp");
            if (resp.status === 404) return null;
            if (!resp.ok) throw new Error("lead_whatsapp_failed");
            return await resp.json();
          }

          function openLeadWhatsappModal(leadId, currentThread) {
            var overlay = document.createElement("div");
            overlay.style.position = "fixed";
            overlay.style.inset = "0";
            overlay.style.background = "rgba(17,24,39,.38)";
            overlay.style.display = "flex";
            overlay.style.alignItems = "center";
            overlay.style.justifyContent = "center";
            overlay.style.padding = "16px";
            overlay.style.zIndex = "5100";
            var dialog = document.createElement("div");
            dialog.style.width = "min(520px, 96vw)";
            dialog.style.maxHeight = "calc(100vh - 48px)";
            dialog.style.overflow = "auto";
            dialog.style.background = "#fff";
            dialog.style.border = "1px solid #d1d5db";
            dialog.style.borderRadius = "16px";
            dialog.style.boxShadow = "0 12px 32px rgba(11,20,26,.18)";
            dialog.style.padding = "14px";
            dialog.innerHTML = '<div style="display:flex;align-items:center;justify-content:space-between;gap:8px;margin-bottom:10px;"><div style="font-size:14px;font-weight:700;">Linkear WhatsApp</div><button type="button" data-close="1" style="width:30px;height:30px;border:1px solid #d1d5db;border-radius:10px;background:#fff;cursor:pointer;">×</button></div><input type="text" placeholder="Buscar thread por nombre o teléfono" style="width:100%;margin-bottom:10px;padding:8px;border:1px solid #d1d5db;border-radius:10px;"><div data-list="1" style="display:grid;gap:8px;"></div><div data-actions="1" style="display:flex;justify-content:flex-end;gap:8px;margin-top:12px;"></div>';
            overlay.appendChild(dialog);
            document.body.appendChild(overlay);
            function close() { if (overlay.parentNode) overlay.parentNode.removeChild(overlay); }
            overlay.addEventListener("click", function(e){ if (e.target === overlay) close(); });
            dialog.querySelector("[data-close='1']").addEventListener("click", close);
            var search = dialog.querySelector("input");
            var list = dialog.querySelector("[data-list='1']");
            var actions = dialog.querySelector("[data-actions='1']");
            if (currentThread && currentThread.thread_id) {
              actions.innerHTML = '<button type="button" style="border:1px solid #d1d5db;border-radius:10px;background:#fff;padding:8px 12px;cursor:pointer;">Unlink</button>';
              actions.querySelector("button").addEventListener("click", function(){
                fetch("/whatsapp/thread/" + currentThread.thread_id + "/unlink-lead", { method: "POST" }).then(function(resp){
                  if (!resp.ok) throw new Error("unlink_failed");
                  close();
                  var btn = document.querySelector('[data-lead-wa-btn="1"][data-lead-id="' + leadId + '"]');
                  if (btn) btn.classList.remove("active");
                }).catch(function(){});
              });
            }
            fetch("/api/whatsapp/threads").then(function(resp){
              if (!resp.ok) throw new Error("threads_failed");
              return resp.json();
            }).then(function(threads){
              function render() {
                var q = waNorm(search ? search.value : "");
                var items = (threads || []).filter(function(thread){
                  var text = [thread.display_name || "", thread.wa_id || "", thread.thread_id].join(" ");
                  return !q || waNorm(text).indexOf(q) !== -1;
                }).slice(0, 40);
                list.innerHTML = items.map(function(thread){
                  return '<button type="button" data-thread-id="' + thread.thread_id + '" style="text-align:left;border:1px solid #e5e7eb;border-radius:12px;background:#fff;padding:10px 12px;cursor:pointer;"><div>' + (thread.display_name || thread.wa_id || "-") + '</div><div style="margin-top:4px;font-size:12px;color:#6b7280;">' + (thread.wa_id || "-") + '</div></button>';
                }).join("") || '<div style="font-size:12px;color:#6b7280;">Sin resultados.</div>';
                list.querySelectorAll("[data-thread-id]").forEach(function(btn){
                  btn.addEventListener("click", function(){
                    fetch("/whatsapp/thread/" + btn.getAttribute("data-thread-id") + "/link-lead", {
                      method: "POST",
                      headers: { "Content-Type": "application/json" },
                      body: JSON.stringify({ lead_id: parseInt(leadId, 10) })
                    }).then(function(resp){
                      if (!resp.ok) throw new Error("link_failed");
                      close();
                      var leadBtn = document.querySelector('[data-lead-wa-btn="1"][data-lead-id="' + leadId + '"]');
                      if (leadBtn) leadBtn.classList.add("active");
                    }).catch(function(){});
                  });
                });
              }
              if (search) search.addEventListener("input", render);
              render();
            }).catch(function(){
              list.innerHTML = '<div style="font-size:12px;color:#6b7280;">No se pudieron cargar los threads.</div>';
            });
          }

          function wireLeadWhatsappButtons() {
            document.querySelectorAll('[data-lead-wa-btn="1"]').forEach(function(btn){
              var leadId = btn.getAttribute("data-lead-id") || "";
              fetchLeadThreadInfo(leadId).then(function(info){
                if (info && info.thread_id) btn.classList.add("active");
              }).catch(function(){});
              btn.addEventListener("click", function(e){
                e.preventDefault();
                fetchLeadThreadInfo(leadId).then(function(info){
                  if (info && info.thread_id) {
                    window.location.href = "/whatsapp/thread/" + info.thread_id;
                    return;
                  }
                  openLeadWhatsappModal(leadId, null);
                }).catch(function(){});
              });
              btn.addEventListener("contextmenu", function(e){
                e.preventDefault();
                fetchLeadThreadInfo(leadId).then(function(info){
                  openLeadWhatsappModal(leadId, info);
                }).catch(function(){
                  openLeadWhatsappModal(leadId, null);
                });
              });
            });
          }

            window.addEventListener("DOMContentLoaded", function () {
              var sbCollapsed = localStorage.getItem("sidebar_collapsed") === "1";
              setSidebarCollapsed(sbCollapsed);
              var hash = window.location.hash || "";
              var match = hash.match(/^#lead-(\\d+)$/);
              if (match) {
                highlightLeadCard(match[1]);
              }
              var leadId = localStorage.getItem("open_revs_lead");
              if (leadId) {
                localStorage.removeItem("open_revs_lead");
                openRevisionArea(leadId);
              }
            var editLeadId = localStorage.getItem("open_edit_latest_lead");
            if (editLeadId) {
              localStorage.removeItem("open_edit_latest_lead");
              openEditLatest(editLeadId);
            }

            var params = new URLSearchParams(window.location.search || "");
            var highlightLeadId = params.get("highlight_lead_id");
            if (highlightLeadId) highlightLeadCard(highlightLeadId);
            var openLead = params.get("open_lead");
            var openRev = params.get("open_rev");
            if (openLead && openRev) {
              var revs = document.getElementById("revs-" + openLead);
              if (revs) revs.open = true;
              var revEl = document.getElementById("rev-" + openLead + "-" + openRev);
              if (revEl) {
                revEl.scrollIntoView({ behavior: "smooth", block: "center" });
                revEl.classList.add("rev-highlight");
                setTimeout(function () { revEl.classList.remove("rev-highlight"); }, 1600);
              }
            }
            syncLeadDetailsHeights();
            applyKanbanSearch();
            wireLeadWhatsappButtons();
            if (searchInput && (searchInput.value || "").trim()) {
              openKanbanSearch(false);
            }

          });

        })();
      </script>
    """)

    html.append("""
      <script>
        (function () {
          var board = document.querySelector('.board');
          var tabs = Array.prototype.slice.call(document.querySelectorAll('.kanban-tab[data-col-index]'));
          var columns = Array.prototype.slice.call(document.querySelectorAll('.kanban-column'));
          if (!board || !tabs.length || !columns.length) return;

          function setActiveTab(index) {
            tabs.forEach(function (t, i) {
              t.classList.toggle('active', i === index);
            });
          }

          // First tab active on load
          setActiveTab(0);

          // Tab click → scroll board to column
          tabs.forEach(function (tab) {
            tab.addEventListener('click', function () {
              var idx = parseInt(tab.getAttribute('data-col-index'), 10);
              board.scrollTo({ left: idx * window.innerWidth, behavior: 'smooth' });
            });
          });

          // IntersectionObserver: mark tab active when column >50% visible
          if (typeof IntersectionObserver !== 'undefined') {
            var observer = new IntersectionObserver(function (entries) {
              entries.forEach(function (entry) {
                if (entry.intersectionRatio > 0.5) {
                  var idx = columns.indexOf(entry.target);
                  if (idx !== -1) setActiveTab(idx);
                }
              });
            }, { root: board, threshold: 0.5 });
            columns.forEach(function (col) { observer.observe(col); });
          }
        })();
      </script>
    """)

    html.append("</main></div>")  # main + layout
    return "\n".join(html)


def render_table_page(
    leads: list[Lead],
    profesionales: list[Profesional] | None = None,
    q: str = "",
    estado: list[str] | None = None,
    flag: list[str] | None = None,
    profesional_id: str = "",
    canal: str = "",
    tipo_vehiculo: str = "",
    marca: str = "",
    modelo: str = "",
    anio: str = "",
    zone_group: str = "",
    zone_detail: str = "",
    estado_revision: str = "",
    turno_fecha_from: str = "",
    turno_fecha_to: str = "",
    zones_map: dict[str, list[str]] | None = None,
    open_filters: bool = False,
) -> str:
    table_css = """
      .tableWrap { overflow-x: auto; max-width: 100%; background: rgba(255,255,255,.7); border: 1px solid var(--border); border-radius: 14px; box-shadow: var(--shadow); max-height: calc(100vh - 160px); }
      table { width: 100%; border-collapse: collapse; min-width: max-content; }
      th, td { padding: 8px 10px; border-bottom: 1px solid var(--border); text-align: left; vertical-align: top; }
      th { position: relative; }
      thead th { font-size: 12px; color: #374151; background: #fff; position: sticky; top: 0; z-index: 5; box-shadow: 0 1px 0 rgba(0,0,0,.08); }
      td { font-size: 13px; }
      tr:hover td { background: #f3f4f6; }
      .tableHeader { display:flex; align-items:center; justify-content:space-between; gap:12px; flex-wrap:wrap; margin-bottom:12px; }
      .tableTopTitle { display:flex; flex-direction:column; gap:2px; }
      .tableSubtitle {
        font-size: 14px;
        font-weight: 700;
        color: #ffffff;
        text-shadow: 0 1px 2px rgba(0,0,0,.4);
        background: rgba(0,0,0,.45);
        backdrop-filter: blur(6px);
        padding: 4px 10px;
        border-radius: 999px;
        display: inline-block;
      }
      .tableTopActions { display:flex; gap:8px; align-items:center; }
      .iconActionBtn { border:1px solid var(--border); background:#fff; border-radius:10px; padding:6px 8px; cursor:pointer; display:inline-flex; align-items:center; }
      .iconActionBtn:hover { background:#f9fafb; }
      @media (max-width: 768px) {
        .kanbanTopBar { display: flex !important; top: 52px; }
        .kanbanTopBarTitle, .buildStamp { display: none !important; }
        .main { padding: 0 12px; }
      }
      .colResizer { position:absolute; right:0; top:0; width:8px; height:100%; cursor:col-resize; }
      body.colResizing { cursor: col-resize; user-select: none; }
      .chips { display:flex; flex-wrap:wrap; gap:8px; margin: 8px 0 12px; }
      .chip { display:inline-flex; align-items:center; gap:8px; padding:6px 10px; border-radius:999px; border:1px solid var(--border); background:#fff; font-size:12px; text-decoration:none; color:#111827; }
      .chip .x { opacity:.6; }
    """
    css = _base_css(extra_css=table_css)

    icon_search = globals().get("ICON_SEARCH")
    icon_export = globals().get("ICON_EXPORT")
    if not icon_search:
        logger.warning("ICON_SEARCH not defined; using text fallback in table header")
        icon_search = "Buscar"
    if not icon_export:
        logger.warning("ICON_EXPORT not defined; using text fallback in table header")
        icon_export = "Exportar"
    build_stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    search_val = html_lib.escape(_val(q), quote=True)

    params = {
        "q": q,
        "estado": estado or [],
        "flag": flag or [],
        "profesional_id": profesional_id,
        "canal": canal,
        "tipo_vehiculo": tipo_vehiculo,
        "marca": marca,
        "modelo": modelo,
        "anio": anio,
        "zone_group": zone_group,
        "zone_detail": zone_detail,
        "estado_revision": estado_revision,
        "turno_fecha_from": turno_fecha_from,
        "turno_fecha_to": turno_fecha_to,
    }
    query = _build_query_string(params)
    kanban_href = f"/kanban?{query}" if query else "/kanban"
    filters_href = "/table"

    def _canal_label(val: str) -> str:
        mapping = {
            "IG_DM": "Instagram DM",
            "IG_WHATSAPP": "Instagram WhatsApp",
            "FB_DM": "Facebook DM",
            "FB_WHATSAPP": "Facebook WhatsApp",
            "WEBSITE": "Website",
            "GOOGLE": "Google",
            "GMAPS": "Google Maps",
            "OTROS": "Otros",
        }
        return mapping.get(val, val.replace("_", " ").title())

    def _make_table_link(new_params: dict[str, Any]) -> str:
        qstr = _build_query_string(new_params)
        return f"/table?{qstr}" if qstr else "/table"

    chips: list[str] = []
    active_params = dict(params)
    estado_list = list(active_params.get("estado") or [])
    if estado_list:
        for st in estado_list:
            p = dict(active_params)
            p["estado"] = [x for x in estado_list if x != st]
            label = KANBAN_LABELS.get(st, st)
            chips.append(f'<a class="chip" href="{_make_table_link(p)}">Estado: {label}<span class="x">-</span></a>')
    flag_list = list(active_params.get("flag") or [])
    if flag_list:
        for fv in flag_list:
            p = dict(active_params)
            p["flag"] = [x for x in flag_list if x != fv]
            label = FLAG_LABELS.get(fv, fv)
            chips.append(f'<a class="chip" href="{_make_table_link(p)}">Flag: {label}<span class="x">-</span></a>')
    if _val(profesional_id):
        p = dict(active_params)
        p["profesional_id"] = ""
        label = "-"
        try:
            pid = int(_val(profesional_id))
        except ValueError:
            pid = None
        if pid:
            prof_lookup = {p.id: p for p in (profesionales or [])}
            prof = prof_lookup.get(pid)
            if prof:
                label = _profesional_label(prof)
        chips.append(f'<a class="chip" href="{_make_table_link(p)}">Profesional: {label}<span class="x">-</span></a>')
    if _val(canal):
        p = dict(active_params)
        p["canal"] = ""
        chips.append(f'<a class="chip" href="{_make_table_link(p)}">Canal: {_canal_label(_val(canal))}<span class="x">-</span></a>')
    if _val(marca):
        p = dict(active_params)
        p["marca"] = ""
        chips.append(f'<a class="chip" href="{_make_table_link(p)}">Marca: {_txt(marca)}<span class="x">-</span></a>')
    if _val(modelo):
        p = dict(active_params)
        p["modelo"] = ""
        chips.append(f'<a class="chip" href="{_make_table_link(p)}">Modelo: {_txt(modelo)}<span class="x">-</span></a>')
    if _val(tipo_vehiculo):
        p = dict(active_params)
        p["tipo_vehiculo"] = ""
        chips.append(f'<a class="chip" href="{_make_table_link(p)}">Tipo vehículo: {_txt(tipo_vehiculo)}<span class="x">-</span></a>')
    if _val(anio):
        p = dict(active_params)
        p["anio"] = ""
        chips.append(f'<a class="chip" href="{_make_table_link(p)}">Año: {_txt(anio)}<span class="x">-</span></a>')
    if _val(zone_group):
        p = dict(active_params)
        p["zone_group"] = ""
        chips.append(f'<a class="chip" href="{_make_table_link(p)}">Zona grupo: {_txt(zone_group)}<span class="x">-</span></a>')
    if _val(zone_detail):
        p = dict(active_params)
        p["zone_detail"] = ""
        chips.append(f'<a class="chip" href="{_make_table_link(p)}">Zona detalle: {_txt(zone_detail)}<span class="x">-</span></a>')
    if _val(estado_revision):
        p = dict(active_params)
        p["estado_revision"] = ""
        chips.append(f'<a class="chip" href="{_make_table_link(p)}">Estado revisión: {_txt(estado_revision)}<span class="x">-</span></a>')
    if _val(turno_fecha_from) or _val(turno_fecha_to):
        p = dict(active_params)
        p["turno_fecha_from"] = ""
        p["turno_fecha_to"] = ""
        if _val(turno_fecha_from) and _val(turno_fecha_to):
            label = f"Turno: {turno_fecha_from} ? {turno_fecha_to}"
        elif _val(turno_fecha_from):
            label = f"Turno desde: {turno_fecha_from}"
        else:
            label = f"Turno hasta: {turno_fecha_to}"
        chips.append(f'<a class="chip" href="{_make_table_link(p)}">{label}<span class="x">-</span></a>')
    if _val(q):
        p = dict(active_params)
        p["q"] = ""
        chips.append(f'<a class="chip" href="{_make_table_link(p)}">Buscar: {_txt(q)}<span class="x">-</span></a>')
    if chips:
        chips.append(f'<a class="chip" href="/table">Limpiar todo<span class="x">-</span></a>')

    filters_form_html = _filters_form_html(
        q=q,
        estado=estado,
        flag=flag,
        profesional_id=profesional_id,
        profesionales=profesionales or [],
        canal=canal,
        tipo_vehiculo=tipo_vehiculo,
        marca=marca,
        modelo=modelo,
        anio=anio,
        zone_group=zone_group,
        zone_detail=zone_detail,
        estado_revision=estado_revision,
        turno_fecha_from=turno_fecha_from,
        turno_fecha_to=turno_fecha_to,
        zones_map=zones_map,
        action="/table",
        include_back_link=True,
        back_href=kanban_href,
        include_open_filters=True,
    )

    total_precio = 0
    for l in leads:
        revs = list(getattr(l, "revisions", []) or [])
        latest = _latest_revision(revs)
        if latest and latest.precio_total is not None:
            total_precio += latest.precio_total

    html: list[str] = [css]
    html.append('<div class="layout">')

    icon_board = '<svg class="icon" viewBox="0 0 24 24"><rect x="3" y="4" width="7" height="7"/><rect x="14" y="4" width="7" height="7"/><rect x="3" y="15" width="7" height="7"/><rect x="14" y="15" width="7" height="7"/></svg>'
    icon_calendar = '<svg class="icon" viewBox="0 0 24 24"><rect x="3" y="5" width="18" height="16" rx="2"/><path d="M8 3v4M16 3v4M3 11h18"/></svg>'
    icon_filter = '<svg class="icon" viewBox="0 0 24 24"><path d="M4 6h16l-6 7v5l-4 2v-7z"/></svg>'
    icon_prof = '<svg class="icon" viewBox="0 0 24 24"><circle cx="12" cy="7" r="4"/><path d="M4 21c1.5-4 14.5-4 16 0"/></svg>'
    icon_ag = '<svg class="icon" viewBox="0 0 24 24"><path d="M3 21h18"/><path d="M5 21V8l7-5 7 5v13"/><path d="M9 12h6"/></svg>'
    icon_toggle = '<svg class="icon icon-only" viewBox="0 0 24 24"><path d="M15 6l-6 6 6 6"/></svg>'

    html.append("""
      <aside class="sidebar" id="sidebar">
        <div class="brandRow">
          <div class="brandText">RIDECHECK</div>
          <button class="sidebarToggle" type="button" onclick="toggleSidebar()" title="Collapse sidebar">%s</button>
        </div>
        %s
      </aside>
    """ % (
        icon_toggle,
        render_sidebar_nav(
            icon_board=icon_board,
            icon_calendar=icon_calendar,
            icon_filter=icon_filter,
            icon_prof=icon_prof,
            icon_ag=icon_ag,
            icon_wa=ICON_WHATSAPP,
            filters_href=filters_href,
        ),
    ))

    html.append('<main class="main">')
    html.append(f"""
      <div class="kanbanTopBar">
        <div class="kanbanTopBarTitle">Base de Datos</div>
        <div class="kanbanTopBarRight">
          <span class="buildStamp">build: {build_stamp}</span>
          <div class="searchControl" id="table-search-control">
            <button class="iconBtn" id="table-search-toggle" type="button" title="Buscar (Ctrl+F)" aria-expanded="false">{ICON_SEARCH}</button>
            <div class="searchBoxWrap" id="table-search-wrap">
              <input id="table-search-input" class="searchInput" type="text" placeholder="Buscar en resultados..." value="{search_val}"/>
              <span id="table-search-count" class="searchCount">0 / 0</span>
              <button class="iconBtn" id="table-search-close" type="button" title="Cerrar búsqueda">{ICON_CLOSE}</button>
            </div>
          </div>
        </div>
      </div>
    """)
    html.append("""
      <div class="tableHeader">
        <div class="tableTopTitle">
          <div class="tableSubtitle">Resultados: %s | Total: %s</div>
        </div>
        <div class="tableTopActions">
          <button class="iconActionBtn" type="button" onclick="openFilters()" title="Filtros" aria-label="Filtros">%s</button>
          <button class="iconActionBtn" type="button" title="Exportar">%s</button>
        </div>
      </div>
    """ % (len(leads), _fmt_money(total_precio), ICON_MENU_HAMBURGER, icon_export))

    rows: list[str] = []
    for l in leads:
        revs = list(getattr(l, "revisions", []) or [])
        latest = _latest_revision(revs)
        flag_val = _lead_flag_value(l)
        flag_label = FLAG_LABELS.get(flag_val, flag_val) if flag_val else None
        flag_html = (
            f'<span class="flagPill flag-{flag_val}">{flag_label}</span>' if flag_val else "-"
        )
        estado_val = _lead_operational_estado(_get(l, "estado"))
        estado_label = KANBAN_LABELS.get(estado_val, estado_val)

        if latest:
            turno_txt = "-"
            if latest.turno_fecha or latest.turno_hora:
                tf = latest.turno_fecha.isoformat() if latest.turno_fecha else "-"
                th = latest.turno_hora.strftime("%H:%M") if latest.turno_hora else "-"
                turno_txt = f"{tf} {th}"
            latest_tipo = _txt(latest.tipo_vehiculo)
            latest_marca = _txt(latest.marca)
            latest_modelo = _txt(latest.modelo)
            latest_anio = _txt(latest.anio)
            latest_zg = _txt(latest.zone_group)
            latest_zd = _txt(latest.zone_detail)
            latest_estado = _txt(latest.estado_revision)
            latest_precio = _fmt_money(latest.precio_total)
        else:
            turno_txt = "-"
            latest_tipo = latest_marca = latest_modelo = latest_anio = "-"
            latest_zg = latest_zd = latest_estado = "-"
            latest_precio = "-"

        open_href = f"{kanban_href}#lead-{l.id}"
        rows.append(f"""
            <tr>
              <td>{l.id}</td>
              <td>{_txt(_get(l, "nombre"))}</td>
              <td>{_txt(_get(l, "apellido"))}</td>
              <td>{_txt(_get(l, "telefono"))}</td>
              <td>{_txt(_get(l, "email"))}</td>
              <td>{estado_label}</td>
              <td>{flag_html}</td>
              <td>{latest_tipo}</td>
              <td>{latest_marca}</td>
              <td>{latest_modelo}</td>
              <td>{latest_anio}</td>
              <td>{latest_zg}</td>
              <td>{latest_zd}</td>
              <td>{turno_txt}</td>
              <td>{latest_estado}</td>
              <td>{latest_precio}</td>
              <td><a class="btn btn-sm" href="{open_href}">Abrir</a></td>
            </tr>
        """)

    html.append("""
      <div id="drawerOverlay" class="drawerOverlay%s" onclick="closeFilters()"></div>
      <div id="filtersDrawer" class="drawer%s" role="dialog" aria-label="Filtros">
        <div class="menuTitle">Filtros</div>
        %s
      </div>
    """ % (" open" if open_filters else "", " open" if open_filters else "", filters_form_html))

    if chips:
        html.append('<div class="chips">%s</div>' % "".join(chips))

    html.append("""
      <div class="tableWrap" data-search-scope="table">
        <table>
          <thead>
            <tr>
              <th>Lead ID<span class="colResizer"></span></th>
              <th>Nombre<span class="colResizer"></span></th>
              <th>Apellido<span class="colResizer"></span></th>
                <th>Tel<span class="colResizer"></span></th>
                <th>Email<span class="colResizer"></span></th>
                <th>Lead Estado<span class="colResizer"></span></th>
                <th>Flag<span class="colResizer"></span></th>
                <th>Tipo vehículo<span class="colResizer"></span></th>
              <th>Marca<span class="colResizer"></span></th>
              <th>Modelo<span class="colResizer"></span></th>
              <th>Año<span class="colResizer"></span></th>
              <th>Zona grupo<span class="colResizer"></span></th>
              <th>Zona detalle<span class="colResizer"></span></th>
              <th>Turno<span class="colResizer"></span></th>
              <th>Estado revisión<span class="colResizer"></span></th>
              <th>Precio total<span class="colResizer"></span></th>
              <th><span class="colResizer"></span></th>
            </tr>
          </thead>
          <tbody>
            %s
          </tbody>
        </table>
      </div>
    """ % "\n".join(rows))

    zones_json = json.dumps(zones_map or {}, ensure_ascii=False).replace("</", "<\\/")
    html.append(f"""
      <script type="application/json" id="zones-data">{zones_json}</script>
    """)

    html.append("""
      <script>
        (function () {
          var zonesEl = document.getElementById("zones-data");
          var zonesMap = {};
          var searchControl = document.getElementById("table-search-control");
          var searchInput = document.getElementById("table-search-input");
          var searchToggleBtn = document.getElementById("table-search-toggle");
          var searchCloseBtn = document.getElementById("table-search-close");
          var searchCount = document.getElementById("table-search-count");
          var searchScope = document.querySelector('[data-search-scope="table"]');
          if (zonesEl && zonesEl.textContent) {
            try {
              zonesMap = JSON.parse(zonesEl.textContent);
            } catch (e) {
              zonesMap = {};
            }
          }
          function normalizeSearchText(value) {
            return (value || "")
              .toString()
              .normalize("NFD")
              .replace(/[\\u0300-\\u036f]/g, "")
              .toLowerCase()
              .trim();
          }
          function applyTableSearch() {
            if (!searchScope) return;
            var q = normalizeSearchText(searchInput ? searchInput.value : "");
            var dataTargets = searchScope.querySelectorAll("[data-search]");
            var total = 0;
            var visible = 0;
            if (dataTargets.length) {
              dataTargets.forEach(function (node) {
                total += 1;
                var haystack = normalizeSearchText(node.getAttribute("data-search") || "");
                var show = !q || haystack.indexOf(q) !== -1;
                node.classList.toggle("search-item-hidden", !show);
                if (show) visible += 1;
              });
            } else {
              var rows = searchScope.querySelectorAll("tbody tr");
              rows.forEach(function (row) {
                total += 1;
                var haystack = normalizeSearchText(row.textContent || "");
                var show = !q || haystack.indexOf(q) !== -1;
                row.style.display = show ? "" : "none";
                if (show) visible += 1;
              });
            }
            if (searchCount) {
              searchCount.textContent = q ? (visible + " / " + total) : (total + " / " + total);
            }
          }
          function openTableSearch(focusInput) {
            if (!searchControl || !searchToggleBtn) return;
            searchControl.classList.add("open");
            searchToggleBtn.setAttribute("aria-expanded", "true");
            if (focusInput && searchInput) {
              searchInput.focus();
              searchInput.select();
            }
            applyTableSearch();
          }
          function closeTableSearch(clearValue) {
            if (!searchControl || !searchToggleBtn) return;
            if (clearValue && searchInput) searchInput.value = "";
            searchControl.classList.remove("open");
            searchToggleBtn.setAttribute("aria-expanded", "false");
            applyTableSearch();
          }
          if (searchToggleBtn) {
            searchToggleBtn.addEventListener("click", function () {
              var isOpen = searchControl && searchControl.classList.contains("open");
              if (isOpen) {
                closeTableSearch(false);
                return;
              }
              openTableSearch(true);
            });
          }
          if (searchCloseBtn) {
            searchCloseBtn.addEventListener("click", function () {
              closeTableSearch(true);
            });
          }
          if (searchInput) {
            searchInput.addEventListener("input", applyTableSearch);
          }
          function refreshZoneDetails(scope) {
            if (!zonesMap || Object.keys(zonesMap).length === 0) return;
            var groupSel = scope.querySelector('select[data-zone-group]');
            var detailSel = scope.querySelector('select[data-zone-detail]');
            if (!groupSel || !detailSel) return;
            var groupVal = groupSel.value || "";
            var options = zonesMap[groupVal] || [];
            var current = detailSel.value || "";
            detailSel.innerHTML = '<option value="">-</option>';
            options.forEach(function (d) {
              var opt = document.createElement("option");
              opt.value = d;
              opt.textContent = d;
              if (d === current) opt.selected = true;
              detailSel.appendChild(opt);
            });
          }

          document.addEventListener("change", function (e) {
            var el = e.target;
            if (el && el.matches('select[data-zone-group]')) {
              var form = el.closest("form") || document;
              refreshZoneDetails(form);
            }
          });

          window.addEventListener("DOMContentLoaded", function () {
            if (!zonesMap || Object.keys(zonesMap).length === 0) return;
            document.querySelectorAll("form").forEach(function (f) {
              refreshZoneDetails(f);
            });
          });

          var FILTERS_STORAGE_KEY = "crm_filters";

          function getFilterForm() {
            return document.querySelector('form[data-filter-form="1"]');
          }

          function readFilters() {
            try {
              var raw = localStorage.getItem(FILTERS_STORAGE_KEY);
              if (!raw) return null;
              var parsed = JSON.parse(raw);
              return parsed && typeof parsed === "object" ? parsed : null;
            } catch (e) {
              return null;
            }
          }

          function writeFilters(form) {
            if (!form) return;
            var data = {};
            var fd = new FormData(form);
            fd.forEach(function (value, key) {
              if (key === "estado") {
                if (!data.estado) data.estado = [];
                data.estado.push(String(value));
              } else {
                data[key] = String(value);
              }
            });
            try {
              localStorage.setItem(FILTERS_STORAGE_KEY, JSON.stringify(data));
            } catch (e) {
              // ignore storage errors
            }
          }

          function restoreFilters(form) {
            if (!form) return;
            var saved = readFilters();
            if (!saved) return;
            var params = new URLSearchParams(window.location.search || "");
            Object.keys(saved).forEach(function (key) {
              if (key === "estado") {
                if (params.has("estado")) return;
                var list = Array.isArray(saved.estado) ? saved.estado : [];
                var set = {};
                list.forEach(function (v) { set[v] = true; });
                form.querySelectorAll('input[name="estado"]').forEach(function (cb) {
                  cb.checked = !!set[cb.value];
                });
                return;
              }
              if (params.has(key)) return;
              var field = form.querySelector('[name="' + key + '"]');
              if (!field) return;
              field.value = saved[key];
            });
            refreshZoneDetails(form);
          }

          function clearSavedFilters(form) {
            try {
              localStorage.removeItem(FILTERS_STORAGE_KEY);
            } catch (e) {
              // ignore storage errors
            }
            if (form) form.reset();
          }

          function initColumnResizers() {
            var minWidth = 120;
            var activeTh = null;
            var startX = 0;
            var startWidth = 0;

            function onMove(e) {
              if (!activeTh) return;
              var dx = e.clientX - startX;
              var width = Math.max(minWidth, startWidth + dx);
              activeTh.style.width = width + "px";
            }

            function onUp() {
              if (!activeTh) return;
              document.removeEventListener("mousemove", onMove);
              document.removeEventListener("mouseup", onUp);
              document.body.classList.remove("colResizing");
              activeTh = null;
            }

            document.querySelectorAll("th .colResizer").forEach(function (handle) {
              handle.addEventListener("mousedown", function (e) {
                e.preventDefault();
                e.stopPropagation();
                var th = handle.closest("th");
                if (!th) return;
                activeTh = th;
                startX = e.clientX;
                startWidth = th.offsetWidth;
                document.addEventListener("mousemove", onMove);
                document.addEventListener("mouseup", onUp);
                document.body.classList.add("colResizing");
              });
            });
          }

          window.openFilters = function () {
            var drawer = document.getElementById("filtersDrawer");
            var overlay = document.getElementById("drawerOverlay");
            if (!drawer || !overlay) return;
            if (!drawer.classList.contains("open")) {
              drawer.classList.add("open");
              overlay.classList.add("open");
              restoreFilters(getFilterForm());
            }
          };

          window.closeFilters = function () {
            var drawer = document.getElementById("filtersDrawer");
            var overlay = document.getElementById("drawerOverlay");
            if (!drawer || !overlay) return;
            drawer.classList.remove("open");
            overlay.classList.remove("open");
          };

          window.toggleSidebar = function () {
            var sidebar = document.getElementById("sidebar");
            if (!sidebar) return;
            sidebar.classList.toggle("collapsed");
          };

          document.addEventListener("click", function (e) {
            document.querySelectorAll("details.multiSelect[open]").forEach(function (d) {
              if (!d.contains(e.target)) d.removeAttribute("open");
            });
          });
          document.addEventListener("keydown", function (e) {
            if (!(e.ctrlKey || e.metaKey)) return;
            if ((e.key || "").toLowerCase() !== "f") return;
            e.preventDefault();
            openTableSearch(true);
          }, true);

          window.addEventListener("DOMContentLoaded", function () {
            initColumnResizers();
          });

          window.addEventListener("DOMContentLoaded", function () {
            var form = getFilterForm();
            if (!form) return;

            var drawer = document.getElementById("filtersDrawer");
            if (drawer && drawer.classList.contains("open")) {
              restoreFilters(form);
            }

            form.addEventListener("submit", function () {
              writeFilters(form);
            });

            var saveBtn = form.querySelector("[data-filter-save]");
            if (saveBtn) {
              saveBtn.addEventListener("click", function () {
                writeFilters(form);
              });
            }

            var restoreBtn = form.querySelector("[data-filter-restore]");
            if (restoreBtn) {
              restoreBtn.addEventListener("click", function () {
                restoreFilters(form);
              });
            }

            var clearBtn = form.querySelector("[data-filter-clear]");
            if (clearBtn) {
              clearBtn.addEventListener("click", function () {
                clearSavedFilters(form);
                var href = clearBtn.getAttribute("data-clear-href");
                if (href) window.location.href = href;
              });
            }
            applyTableSearch();
            if (searchInput && (searchInput.value || "").trim()) {
              openTableSearch(false);
            }
          });
        })();
      </script>
    """)

    html.append("</main></div>")
    return "\n".join(html)


def render_calendar_page(
    leads: list[Lead],
    profesionales: list[Profesional] | None = None,
    week: str | None = None,
    user_email: str = "",
    highlight_lead_id: int | None = None,
) -> str:
    base_monday: date | None = None
    if week:
        try:
            base_monday = date.fromisoformat(str(week).strip())
        except ValueError:
            base_monday = None
    if base_monday is None:
        today = date.today()
        base_monday = today - timedelta(days=today.weekday())
    now = datetime.now()

    week_start = base_monday
    week_end = week_start + timedelta(days=6)
    prev_monday = week_start - timedelta(days=7)
    next_monday = week_start + timedelta(days=7)

    def day_label(d: date) -> str:
        labels = ["Lun", "Mar", "Mie", "Jue", "Vie", "Sab", "Dom"]
        return f"{labels[d.weekday()]} {d.strftime('%d/%m')}"

    profesionales = profesionales or []
    prof_by_id = {p.id: p for p in profesionales}

    items: list[dict[str, Any]] = []
    for l in leads:
        revs = list(_get(l, "revisions") or [])
        for r in revs:
            if not r.turno_fecha:
                continue
            if r.turno_fecha < week_start or r.turno_fecha > week_end:
                continue
            items.append({
                "lead": l,
                "rev": r,
                "day": r.turno_fecha,
                "time": r.turno_hora,
            })

    def sort_key(it: dict[str, Any]) -> tuple[date, time, int, int]:
        t = it["time"] if it["time"] else time.max
        lead_id = _get(it["lead"], "id") or 0
        rev_id = _get(it["rev"], "id") or 0
        return (it["day"], t, lead_id, rev_id)

    items.sort(key=sort_key)

    by_day: dict[date, list[dict[str, Any]]] = {week_start + timedelta(days=i): [] for i in range(7)}
    for it in items:
        by_day[it["day"]].append(it)

    today = date.today()
    import calendar as _cal_mod
    month_start = week_start.replace(day=1)
    month_last = _cal_mod.monthrange(month_start.year, month_start.month)[1]
    month_end_dt = month_start.replace(day=month_last)
    month_appts: list[dict[str, Any]] = []
    for _ml0 in leads:
        for _mr0 in (_get(_ml0, "revisions") or []):
            if not _mr0.turno_fecha:
                continue
            if _mr0.turno_fecha < month_start or _mr0.turno_fecha > month_end_dt:
                continue
            month_appts.append({"lead": _ml0, "rev": _mr0, "day": _mr0.turno_fecha, "time": _mr0.turno_hora})
    month_appts.sort(key=sort_key)
    by_day_month: dict[date, list[dict[str, Any]]] = {}
    for _mit0 in month_appts:
        by_day_month.setdefault(_mit0["day"], []).append(_mit0)

    _MONTHS_ES_CAP = ["Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio", "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre"]
    week_label_str = f"{week_start.strftime('%d/%m')} – {week_end.strftime('%d/%m')}"
    month_label_str = f"{_MONTHS_ES_CAP[month_start.month - 1]} {month_start.year}"
    _prev_month_first = (month_start - timedelta(days=1)).replace(day=1)
    prev_month_monday = _prev_month_first - timedelta(days=_prev_month_first.weekday())
    _next_month_first = month_end_dt + timedelta(days=1)
    next_month_monday = _next_month_first - timedelta(days=_next_month_first.weekday())

    calendar_css = """
      /* ── Calendar: shared font ── */
      .calWrap * { font-family: 'Bahnschrift','Segoe UI','Arial Narrow',Arial,sans-serif; }
      /* Fix: push content below fixed top nav on mobile */
      @media (max-width: 768px) { .main { padding-top: 56px !important; } }
      /* Desktop: center and cap at a comfortable wide width */
      @media (min-width: 769px) {
        .calWrap { max-width: min(94vw, 1360px); margin: 0 auto; }
      }

      /* ── View pills ── */
      .calViewPills {
        display: flex; gap: 6px; justify-content: center;
        padding: 8px 12px;
        background: rgba(255,255,255,.11); border-radius: 12px; margin: 6px 12px 4px;
      }
      .calPill {
        padding: 5px 18px; border-radius: 999px; font-size: 12.5px; font-weight: 700;
        border: 2px solid rgba(255,255,255,.45); background: transparent;
        cursor: pointer; color: rgba(255,255,255,.65);
        transition: background .15s, color .15s, border-color .15s;
        font-family: 'Bahnschrift','Segoe UI','Arial Narrow',Arial,sans-serif;
      }
      .calPill.active { background: #111827; color: #fff; border-color: #111827; }
      .calPill:hover:not(.active) { background: rgba(255,255,255,.1); color: #fff; }

      /* ── Date header ── */
      .calDateHeader {
        display: flex; align-items: center; justify-content: center;
        gap: 12px; padding: 2px 0 6px;
      }
      .calNavArrow {
        background: none; border: 2px solid rgba(255,255,255,.3); border-radius: 50%;
        width: 30px; height: 30px; font-size: 17px; cursor: pointer; color: #fff;
        display: inline-flex; align-items: center; justify-content: center;
        flex-shrink: 0; transition: background .15s, border-color .15s;
      }
      .calNavArrow:hover { background: rgba(255,255,255,.15); border-color: rgba(255,255,255,.65); }
      .calDateHeadCenter { text-align: center; min-width: 150px; }
      .calDateBig {
        font-size: 1.25rem; font-weight: 800; color: #fff; line-height: 1.2;
        font-family: 'Bahnschrift','Segoe UI','Arial Narrow',Arial,sans-serif;
        letter-spacing: .01em; text-align: center;
      }
      @media (min-width: 769px) {
        .calDateBig { font-size: 1.75rem; }
      }
      .calDateSub {
        font-size: .78rem; font-weight: 500; color: rgba(255,255,255,.6);
        text-transform: capitalize; margin-top: 1px; text-align: center;
        font-family: 'Bahnschrift','Segoe UI','Arial Narrow',Arial,sans-serif;
      }

      /* ── View panels ── */
      .calViewPanel { display: none; }
      .calViewPanel.active { display: block; }

      /* ── Day view ── */
      .calDaySlots { display: flex; flex-direction: column; gap: 2px; padding: 6px 12px; }
      /* Empty hour slots: thin, barely-there marker */
      .calDaySlotCard {
        display: flex; align-items: center;
        border-radius: 7px; overflow: hidden;
        background: rgba(255,255,255,.04);
        min-height: 26px;
      }
      /* Appointment cards: stand out with white bg and subtle shadow */
      .calDaySlotCard.has-appt {
        background: #fff; box-shadow: 0 1px 6px rgba(0,0,0,.11);
        border-radius: 11px; min-height: 52px; align-items: stretch;
      }
      .calDaySlotCard.has-appt:hover { box-shadow: 0 3px 12px rgba(0,0,0,.2); }
      .calDaySlotCard.past-appt { background: #f3f4f6; }
      .calDaySlotCard.confirmed-appt { background: #ecfdf5; }
      .calDaySlotCard.pending-appt { background: #fff7ed; }
      .calDaySlotTime {
        flex: 0 0 46px; display: flex; align-items: center; justify-content: center;
        font-size: 10.5px; font-weight: 600; color: rgba(255,255,255,.22);
        padding: 0 4px;
        font-family: 'Bahnschrift','Segoe UI','Arial Narrow',Arial,sans-serif;
      }
      .calDaySlotCard.has-appt .calDaySlotTime {
        color: #b0b7c3; font-size: 10.5px; font-weight: 700;
        align-items: flex-start; padding-top: 10px;
      }
      .calDaySlotBody {
        flex: 1; min-width: 0; padding: 9px 12px 8px 0;
        text-decoration: none; color: inherit; display: block;
      }
      /* Typography hierarchy: name > vehicle > address/prof */
      .calApptName {
        font-size: .87rem; font-weight: 800; color: #111827; line-height: 1.25;
        font-family: 'Bahnschrift','Segoe UI','Arial Narrow',Arial,sans-serif;
      }
      /* First meta (vehicle): medium-dark */
      .calApptName + .calApptMeta { color: #374151; font-weight: 600; font-size: 11px; }
      /* Second meta (address/prof): soft */
      .calApptMeta ~ .calApptMeta { color: #9ca3af; font-weight: 400; font-size: 10.5px; }
      .calApptMeta { margin-top: 2px; line-height: 1.35; }
      .calApptMeta a { color: #3b82f6; text-decoration: none; }
      .calApptStatus {
        display: inline-block; font-size: 9.5px; font-weight: 700; letter-spacing: .025em;
        border-radius: 999px; padding: 1px 6px; margin-top: 4px;
        background: #e0f2fe; color: #0369a1;
      }
      .calApptStatus.confirmed { background: #dcfce7; color: #166534; }
      .calApptStatus.past { background: #f1f5f9; color: #94a3b8; }

      /* ── Week view ── */
      .calWeekList { display: flex; flex-direction: column; gap: 5px; padding: 6px 12px; }
      .calWeekDayCard { border-radius: 12px; overflow: hidden; background: rgba(255,255,255,.06); }
      .calWeekDayCard.has-appt { background: #fff; box-shadow: 0 1px 6px rgba(0,0,0,.1); }
      .calWeekDayHead {
        display: flex; align-items: center; justify-content: space-between; padding: 6px 12px;
      }
      .calWeekDayCard.has-appt .calWeekDayHead { border-bottom: 1px solid #f3f4f6; }
      .calWeekDayName {
        font-size: 11px; font-weight: 800; color: rgba(255,255,255,.4);
        font-family: 'Bahnschrift','Segoe UI','Arial Narrow',Arial,sans-serif;
        text-transform: uppercase; letter-spacing: .07em;
      }
      .calWeekDayCard.has-appt .calWeekDayName { color: #374151; }
      .calWeekDayDate { font-size: 11px; font-weight: 500; color: rgba(255,255,255,.28); }
      .calWeekDayCard.has-appt .calWeekDayDate { color: #9ca3af; }
      .calWeekApptRow {
        display: flex; align-items: flex-start; gap: 8px;
        padding: 8px 12px; border-top: 1px solid #f3f4f6;
        text-decoration: none; color: inherit;
      }
      .calWeekApptRow:hover { background: #f9fafb; }
      .calWeekApptInfo { order: 1; flex: 1; min-width: 0; }
      .calWeekApptTime {
        order: 2; flex: 0 0 40px; margin-left: auto; padding-left: 6px; flex-shrink: 0;
        font-size: 11px; font-weight: 700; color: #9ca3af;
        padding-top: 2px; text-align: right;
        font-family: 'Bahnschrift','Segoe UI','Arial Narrow',Arial,sans-serif;
      }
      /* ribbon case */
      .calApptBody {
        display: flex; flex-direction: row; gap: 8px; align-items: flex-start;
        flex: 1; min-width: 0; padding: 3px 8px;
      }
      .calApptBody .calWeekApptInfo { order: 1; flex: 1; min-width: 0; }
      .calApptBody .calWeekApptTime { order: 2; margin-left: auto; }
      .calWeekApptName {
        font-size: .85rem; font-weight: 800; color: #111827;
        font-family: 'Bahnschrift','Segoe UI','Arial Narrow',Arial,sans-serif;
      }
      /* First meta after name (vehicle): slightly darker */
      .calWeekApptName + .calWeekApptMeta { color: #4b5563; font-weight: 600; }
      /* Second meta (address/prof): muted */
      .calWeekApptMeta ~ .calWeekApptMeta { color: #9ca3af; font-weight: 400; font-size: 10.5px; }
      .calWeekApptMeta { font-size: 11px; color: #6b7280; margin-top: 1px; line-height: 1.35; }
      .calWeekApptMeta a { color: #3b82f6; text-decoration: none; }

      /* ── Month view ── */
      .calMonthWrap { padding: 6px 12px; }
      .calMonthGrid { display: grid; grid-template-columns: repeat(7,1fr); gap: 2px; margin-bottom: 8px; }
      .calMonthDayHead {
        font-size: 9px; font-weight: 800; color: rgba(255,255,255,.38);
        text-align: center; padding: 3px 0; text-transform: uppercase; letter-spacing: .05em;
      }
      .calMonthCell { min-height: 36px; border-radius: 8px; padding: 3px 2px; text-align: center; cursor: pointer; position: relative; }
      .calMonthCell:hover { background: rgba(255,255,255,.1); }
      .calMonthCell.today-cell .calMonthNum {
        background: #fff !important; color: #111827 !important; border-radius: 50%;
        width: 26px; height: 26px; line-height: 26px; display: inline-block; padding: 0;
      }
      .calMonthCell.selected-cell { background: rgba(255,255,255,.12); }
      .calMonthCell.other-month { opacity: .28; }
      .calMonthNum {
        display: block; font-size: .85rem; font-weight: 700; line-height: 1.9; color: #fff;
        font-family: 'Bahnschrift','Segoe UI','Arial Narrow',Arial,sans-serif;
      }
      .calMonthDot { display: block; width: 5px; height: 5px; border-radius: 50%; background: #ef4444; margin: 0 auto; }
      .calMonthDetail { border-radius: 13px; background: rgba(255,255,255,.07); padding: 10px; margin-top: 4px; }
      .calMonthDetailTitle {
        font-size: .83rem; font-weight: 800; color: rgba(255,255,255,.82); margin-bottom: 8px;
        font-family: 'Bahnschrift','Segoe UI','Arial Narrow',Arial,sans-serif;
      }
      .calMonthDetailList { display: flex; flex-direction: column; gap: 5px; }
      .calMonthApptCard {
        background: #fff; border-radius: 9px; padding: 7px 10px;
        text-decoration: none; color: inherit; display: block;
        box-shadow: 0 1px 4px rgba(0,0,0,.09);
      }
      .calMonthApptCard:hover { box-shadow: 0 2px 10px rgba(0,0,0,.17); }
      .calMonthApptCard.past { background: #f9fafb; }
      .calMonthApptCard.confirmed { background: #f0fdf4; }
      .calMonthApptCard.pending { background: #fff7ed; }
      .calMonthApptName {
        font-size: .83rem; font-weight: 800; color: #111827;
        font-family: 'Bahnschrift','Segoe UI','Arial Narrow',Arial,sans-serif;
      }
      /* Month card: vehicle line slightly darker, address softer */
      .calMonthApptName + .calMonthApptMeta { color: #4b5563; font-weight: 600; font-size: 10.5px; }
      .calMonthApptMeta ~ .calMonthApptMeta { color: #9ca3af; font-size: 10px; }
      .calMonthApptMeta { font-size: 10.5px; color: #6b7280; margin-top: 2px; line-height: 1.35; }
      .calMonthApptMeta a { color: #3b82f6; text-decoration: none; }

      /* ── Desktop: spacious card typography and nav ── */
      @media (min-width: 769px) {
        .calNavArrow { width: 36px; height: 36px; font-size: 19px; }
        .calDateHeadCenter { min-width: 220px; }
        .calDateSub { font-size: .9rem; }
        .calDaySlots { gap: 3px; padding: 8px 16px; }
        .calDaySlotTime { flex: 0 0 56px; font-size: 12px; }
        .calDaySlotCard.has-appt .calDaySlotTime { font-size: 12px; padding-top: 11px; }
        .calDaySlotBody { padding: 11px 18px 10px 0; }
        .calApptName { font-size: .95rem; }
        .calApptName + .calApptMeta { font-size: 12px; }
        .calApptMeta ~ .calApptMeta { font-size: 11.5px; }
        .calApptStatus { font-size: 10.5px; padding: 2px 8px; }
        .calWeekList { gap: 6px; padding: 8px 16px; }
        .calWeekDayHead { padding: 8px 16px; }
        .calWeekDayName { font-size: 12px; }
        .calWeekDayDate { font-size: 12px; }
        .calWeekApptRow { padding: 10px 16px; }
        .calWeekApptName { font-size: .92rem; }
        .calWeekApptName + .calWeekApptMeta { font-size: 12px; }
        .calWeekApptMeta ~ .calWeekApptMeta { font-size: 11.5px; }
        .calWeekApptMeta { font-size: 12px; }
        .calWeekApptTime { font-size: 12px; flex: 0 0 46px; }
        .calMonthWrap { padding: 8px 16px; }
        .calMonthApptCard { padding: 9px 12px; }
        .calMonthApptName { font-size: .9rem; }
        .calMonthApptName + .calMonthApptMeta { font-size: 12px; }
        .calMonthApptMeta ~ .calMonthApptMeta { font-size: 11px; }
        .calMonthApptMeta { font-size: 12px; }
        .calMonthDetailTitle { font-size: .9rem; }
      }

      /* ── Highlight ── */
      .calAppt-highlight { outline: 3px solid #f59e0b; outline-offset: 2px;
        animation: calHighlightPulse 2s ease-out .2s forwards; }
      @keyframes calHighlightPulse {
        0%   { box-shadow: 0 0 0 10px rgba(245,158,11,.45); }
        100% { box-shadow: 0 0 0  4px rgba(245,158,11,.10); }
      }

      /* ── Approval ribbon ── */
      .calApptApproval { display:flex; align-items:stretch; gap:0; min-height:52px; }
      .calStatusRibbon {
        position:relative; flex:0 0 22px; width:22px; margin:1px 0 1px 1px;
        display:flex; align-items:center; justify-content:center;
        border-radius:8px 0 0 8px; background:linear-gradient(180deg,#fffdf4,#f8efcf);
        border-right:1px solid rgba(207,148,27,.18); overflow:hidden;
      }
      .calStatusRibbonInner {
        position:absolute; top:50%; left:50%;
        transform:translate(-50%,-50%) rotate(-90deg);
        display:flex; flex-direction:column; align-items:center; gap:2px; white-space:nowrap;
      }
      .calStatusRibbonWord { display:block; font-size:7px; font-weight:800; letter-spacing:.05em; text-transform:uppercase; color:#b88200; }
      .calApptApproval .calApptBody { flex:1 1 auto; min-width:0; padding:2px 8px 2px 6px; }
    """

    css = _base_css(extra_css=calendar_css)

    icon_board = '<svg class="icon" viewBox="0 0 24 24"><rect x="3" y="4" width="7" height="7"/><rect x="14" y="4" width="7" height="7"/><rect x="3" y="15" width="7" height="7"/><rect x="14" y="15" width="7" height="7"/></svg>'
    icon_calendar = '<svg class="icon" viewBox="0 0 24 24"><rect x="3" y="5" width="18" height="16" rx="2"/><path d="M8 3v4M16 3v4M3 11h18"/></svg>'
    icon_filter = '<svg class="icon" viewBox="0 0 24 24"><path d="M4 6h16l-6 7v5l-4 2v-7z"/></svg>'
    icon_prof = '<svg class="icon" viewBox="0 0 24 24"><circle cx="12" cy="7" r="4"/><path d="M4 21c1.5-4 14.5-4 16 0"/></svg>'
    icon_ag = '<svg class="icon" viewBox="0 0 24 24"><path d="M3 21h18"/><path d="M5 21V8l7-5 7 5v13"/><path d="M9 12h6"/></svg>'
    icon_toggle = '<svg class="icon icon-only" viewBox="0 0 24 24"><path d="M15 6l-6 6 6 6"/></svg>'
    build_stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    html: list[str] = [css]
    html.append('<div class="layout">')
    html.append("""
      <aside class="sidebar" id="sidebar">
        <div class="brandRow">
          <div class="brandText">RIDECHECK</div>
          <button class="sidebarToggle" type="button" onclick="toggleSidebar()" title="Collapse sidebar">%s</button>
        </div>
        %s
        %s
      </aside>
    """ % (
        icon_toggle,
        render_sidebar_nav(
            icon_board=icon_board,
            icon_calendar=icon_calendar,
            icon_filter=icon_filter,
            icon_prof=icon_prof,
            icon_ag=icon_ag,
            icon_wa=ICON_WHATSAPP,
        ),
        _sidebar_user_block(user_email),
    ))

    html.append('<div id="popover-root"></div>')

    html.append('<main class="main">')
    html.append(f"""
      <div class="kanbanTopBar">
        <div class="kanbanTopBarTitle">Calendario</div>
        <div class="kanbanTopBarRight">
          <span class="buildStamp">build: {build_stamp}</span>
          <div class="searchControl" id="calendar-search-control">
            <button class="iconBtn" id="calendar-search-toggle" type="button" title="Buscar (Ctrl+F)" aria-expanded="false">{ICON_SEARCH}</button>
            <div class="searchBoxWrap" id="calendar-search-wrap">
              <input id="calendar-search-input" class="searchInput" type="text" placeholder="Buscar turnos..." value=""/>
              <span id="calendar-search-count" class="searchCount">0 / 0</span>
              <button class="iconBtn" id="calendar-search-close" type="button" title="Cerrar búsqueda">{ICON_CLOSE}</button>
            </div>
          </div>
        </div>
      </div>
    """)
    html.append("""
      <div class="calTopBar">
        <a class="btn btn-sm" href="/calendar">Hoy</a>
      </div>
    """)
    _MONTHS_ES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"]
    _MONTHS_CAP = ["Enero","Febrero","Marzo","Abril","Mayo","Junio","Julio","Agosto","Septiembre","Octubre","Noviembre","Diciembre"]
    _DAY_SHORT = ["Lun","Mar","Mié","Jue","Vie","Sáb","Dom"]
    _DAYS_FULL = ["Lunes","Martes","Miércoles","Jueves","Viernes","Sábado","Domingo"]

    # ── helpers ────────────────────────────────────────────────────
    def _acls(rev_obj):
        _cdt = datetime.combine(rev_obj.turno_fecha, rev_obj.turno_hora if rev_obj.turno_hora else time.min) if rev_obj.turno_fecha else None
        _past = _cdt is not None and _cdt < now
        _ev = (rev_obj.estado_revision or "").strip().upper()
        return "past" if _past else ("confirmed" if _ev == "CONFIRMADO" else "pending")

    def _ahref(l, r): return f"/kanban?open_lead={l.id}&open_rev={r.id}"
    def _aname(l): return (f"{(_get(l,'nombre') or '').strip()} {(_get(l,'apellido') or '').strip()}").strip() or "-"
    def _aveh(r):
        tipo = _friendly_tipo_vehiculo(_val(r.tipo_vehiculo))
        return " / ".join([x for x in [tipo, _val(r.marca), _val(r.modelo), str(r.anio) if r.anio else ""] if x])
    def _aaddr(r): return _url_link(r.link_maps) if _safe_url(r.link_maps) else _txt(r.direccion_texto)
    def _aprof(r):
        pid = getattr(r, "profesional_id", None)
        p = prof_by_id.get(pid) if pid else None
        return _profesional_label(p) if p else ""
    def _ameta(*parts) -> str:
        """Join non-empty parts with · — skips blank and '-' values."""
        kept = [str(p) for p in parts if p and str(p).strip() not in ("", "-")]
        return " &nbsp;&middot;&nbsp; ".join(kept)

    def _day_slots(appts):
        by_hr = {}
        no_hr = []
        for it in appts:
            h = it["rev"].turno_hora.hour if it["rev"].turno_hora else None
            (by_hr.setdefault(h, []) if h is not None else no_hr).append(it)
        parts = ['<div class="calDaySlots">']
        for h in range(8, 19):
            sa = by_hr.get(h, [])
            if sa:
                for it in sa:
                    l, r = it["lead"], it["rev"]
                    cls = _acls(r); href = _ahref(l, r)
                    tstr = r.turno_hora.strftime("%H:%M") if r.turno_hora else f"{h:02d}:00"
                    scls = "confirmed" if cls == "confirmed" else ("past" if cls == "past" else "")
                    _meta_row = _ameta(_aaddr(r), _aprof(r))
                    parts.append(
                        f'<div class="calDaySlotCard has-appt {cls}-appt">'
                        f'<div class="calDaySlotTime">{tstr}</div>'
                        f'<a class="calDaySlotBody" href="{href}">'
                        f'<div class="calApptName">{_txt(_aname(l))}</div>'
                        f'<div class="calApptMeta">{_txt(_aveh(r))}</div>'
                        + (f'<div class="calApptMeta">{_meta_row}</div>' if _meta_row else '')
                        + f'<span class="calApptStatus {scls}">{_txt(r.estado_revision)}</span>'
                        '</a></div>'
                    )
            else:
                parts.append(
                    f'<div class="calDaySlotCard">'
                    f'<div class="calDaySlotTime">{h:02d}:00hs</div>'
                    '<div class="calDaySlotBody"></div></div>'
                )
        for it in no_hr:
            l, r = it["lead"], it["rev"]
            cls = _acls(r); href = _ahref(l, r)
            scls = "confirmed" if cls == "confirmed" else ("past" if cls == "past" else "")
            _meta_row_nr = _ameta(_aaddr(r), _aprof(r))
            parts.append(
                f'<div class="calDaySlotCard has-appt {cls}-appt">'
                '<div class="calDaySlotTime">--</div>'
                f'<a class="calDaySlotBody" href="{href}">'
                f'<div class="calApptName">{_txt(_aname(l))}</div>'
                f'<div class="calApptMeta">{_txt(_aveh(r))}</div>'
                + (f'<div class="calApptMeta">{_meta_row_nr}</div>' if _meta_row_nr else '')
                + f'<span class="calApptStatus {scls}">{_txt(r.estado_revision)}</span>'
                '</a></div>'
            )
        parts.append('</div>')
        return "".join(parts)

    def _month_card(it):
        l, r = it["lead"], it["rev"]
        cls = _acls(r); href = _ahref(l, r)
        tstr = r.turno_hora.strftime("%H:%M") if r.turno_hora else "-"
        hl_id = ' id="cal-highlight-appt"' if (highlight_lead_id and _get(l,"id") == highlight_lead_id) else ""
        hl_cls = " calAppt-highlight" if hl_id else ""
        _meta_mc = _ameta(_aaddr(r), _aprof(r))
        return (
            f'<a class="calMonthApptCard {cls}{hl_cls}" href="{href}"{hl_id}>'
            f'<div class="calMonthApptName">{_txt(_aname(l))} <span style="font-weight:400;color:#9ca3af;font-size:12px;">{tstr}</span></div>'
            f'<div class="calMonthApptMeta">{_txt(_aveh(r))}</div>'
            + (f'<div class="calMonthApptMeta">{_meta_mc}</div>' if _meta_mc else '')
            + f'<div class="calMonthApptMeta">{_txt(r.estado_revision)}</div>'
            '</a>'
        )

    # ── pill + date header (shared) ────────────────────────────────
    week_big = f"{week_start.day} {_MONTHS_CAP[week_start.month-1]} / {week_end.day} {_MONTHS_CAP[week_end.month-1]}"
    month_big = f"{_MONTHS_CAP[month_start.month-1]} / {month_start.year}"

    html.append('<div class="calWrap">')
    html.append(f"""
      <div class="calViewPills" id="cal-pills">
        <button class="calPill active" data-view="day" type="button">Día</button>
        <button class="calPill" data-view="week" type="button">Semana</button>
        <button class="calPill" data-view="month" type="button">Mes</button>
      </div>
      <div id="cal-date-header" class="calDateHeader"
        data-week-big="{html_lib.escape(week_big, quote=True)}"
        data-week-sub="{week_start.year}"
        data-month-big="{html_lib.escape(month_big, quote=True)}"
        data-prev-week="/calendar?week={prev_monday.isoformat()}"
        data-next-week="/calendar?week={next_monday.isoformat()}"
        data-prev-month="/calendar?week={prev_month_monday.isoformat()}"
        data-next-month="/calendar?week={next_month_monday.isoformat()}">
        <button id="cal-nav-prev" class="calNavArrow" type="button">&#8249;</button>
        <div class="calDateHeadCenter">
          <div id="cal-date-big" class="calDateBig"></div>
          <div id="cal-date-sub" class="calDateSub"></div>
        </div>
        <button id="cal-nav-next" class="calNavArrow" type="button">&#8250;</button>
      </div>
    """)

    # ── day view ───────────────────────────────────────────────────
    html.append(f'<div id="cal-view-day" class="calViewPanel active" data-today="{today.isoformat()}">')
    html.append(_day_slots(by_day_month.get(today, [])))
    html.append('</div>')

    # ── week view ──────────────────────────────────────────────────
    html.append('<div id="cal-view-week" class="calViewPanel" data-search-scope="calendar">')
    html.append('<div class="calWeekList">')
    for i in range(7):
        day = week_start + timedelta(days=i)
        appts = by_day.get(day, [])
        has_cls = " has-appt" if appts else ""
        dn = _DAY_SHORT[day.weekday()]
        dm = f"{day.day} {_MONTHS_CAP[day.month-1]}"
        today_sfx = " (Hoy)" if day == today else ""
        html.append(f'<div class="calWeekDayCard{has_cls}">')
        html.append(f'<div class="calWeekDayHead"><span class="calWeekDayName">{dn}{today_sfx}</span><span class="calWeekDayDate">{dm}</span></div>')
        for it in appts:
            l, r = it["lead"], it["rev"]
            cls = _acls(r); href = _ahref(l, r)
            tstr = r.turno_hora.strftime("%H:%M") if r.turno_hora else "-"
            hl_id = ' id="cal-highlight-appt"' if (highlight_lead_id and _get(l,"id") == highlight_lead_id) else ""
            hl_cls = " calAppt-highlight" if hl_id else ""
            search_text = html_lib.escape(" ".join([_val(_aname(l)), _val(_aveh(r)), _val(r.direccion_texto), _val(_aprof(r)), _val(r.estado_revision)]), quote=True)
            appt_dt = datetime.combine(r.turno_fecha, r.turno_hora if r.turno_hora else time.min) if r.turno_fecha else None
            is_past = appt_dt is not None and appt_dt < now
            approval_status = _revision_approval_status(r)
            approval_ui = _render_revision_approval_ui(r)
            ribbon = body_close = ""
            if approval_status == "PENDING" and not is_past:
                ribbon = ('<div class="calApptApproval"><div class="calStatusRibbon"><div class="calStatusRibbonInner">'
                          '<span class="calStatusRibbonWord">ESPERANDO</span><span class="calStatusRibbonWord">APROBACIÓN</span>'
                          '</div></div><div class="calApptBody">')
                body_close = "</div></div>"
            _meta_wk = _ameta(_aaddr(r), _aprof(r))
            html.append(
                f'<a class="calWeekApptRow{hl_cls}" href="{href}" data-search="{search_text}"{hl_id}>'
                + ribbon
                + f'<span class="calWeekApptTime">{tstr}</span>'
                + f'<div class="calWeekApptInfo"><div class="calWeekApptName">{_txt(_aname(l))}</div>'
                + f'<div class="calWeekApptMeta">{_txt(_aveh(r))}</div>'
                + (f'<div class="calWeekApptMeta">{_meta_wk}</div>' if _meta_wk else '')
                + f'<div class="calWeekApptMeta">{_txt(r.estado_revision)}</div>'
                + approval_ui + body_close
                + '</div></a>'
            )
        html.append('</div>')
    html.append('</div>')  # calWeekList
    html.append('</div>')  # cal-view-week

    # ── month view ─────────────────────────────────────────────────
    _MGRID_START = month_start - timedelta(days=month_start.weekday())
    html.append('<div id="cal-view-month" class="calViewPanel">')
    html.append('<div class="calMonthWrap">')
    html.append('<div class="calMonthGrid">')
    for _dh in ["L","M","M","J","V","S","D"]:
        html.append(f'<div class="calMonthDayHead">{_dh}</div>')
    for _gi in range(42):
        _gd = _MGRID_START + timedelta(days=_gi)
        _tcls = " today-cell" if _gd == today else ""
        _ocls = " other-month" if (_gd.month != month_start.month or _gd.year != month_start.year) else ""
        _da = by_day_month.get(_gd, [])
        if _da:
            _any_ok = any((ax["rev"].estado_revision or "").strip().upper() == "CONFIRMADO" for ax in _da)
            _dot = f'<span class="calMonthDot{" confirmed" if _any_ok else ""}"></span>'
        else:
            _dot = ""
        html.append(f'<div class="calMonthCell{_tcls}{_ocls}" data-date="{_gd.isoformat()}"><span class="calMonthNum">{_gd.day}</span>{_dot}</div>')
    html.append('</div>')  # calMonthGrid
    html.append('<div id="cal-month-detail" class="calMonthDetail"></div>')
    html.append('<div id="cal-month-days-data" style="display:none;">')
    for _gi2 in range(42):
        _gd2 = _MGRID_START + timedelta(days=_gi2)
        _dappts = by_day_month.get(_gd2, [])
        _dlabel = f"{_gd2.day} {_MONTHS_CAP[_gd2.month-1]}"
        html.append(f'<div id="cal-mday-{_gd2.isoformat()}">')
        html.append(f'<div class="calMonthDetailTitle">{_dlabel}</div><div class="calMonthDetailList">')
        if not _dappts:
            html.append('<div style="color:rgba(255,255,255,.4);font-size:13px;">Sin turnos.</div>')
        else:
            for _da2 in _dappts:
                html.append(_month_card(_da2))
        html.append('</div></div>')  # calMonthDetailList + cal-mday
    html.append('</div>')  # cal-month-days-data
    # Pre-render day-view slots for every day in the grid so JS can swap them client-side
    html.append('<div id="cal-dayslots-data" style="display:none;">')
    for _gi3 in range(42):
        _gd3 = _MGRID_START + timedelta(days=_gi3)
        _dappts3 = by_day_month.get(_gd3, [])
        html.append(f'<div id="cal-dayslots-{_gd3.isoformat()}">')
        html.append(_day_slots(_dappts3))
        html.append('</div>')
    html.append('</div>')  # cal-dayslots-data
    html.append('</div>')  # calMonthWrap
    html.append('</div>')  # cal-view-month
    html.append('</div>')  # calWrap

    # ── scripts ────────────────────────────────────────────────────
    html.append("""
      <script>
        (function () {
          var sc=document.getElementById("calendar-search-control"),
              si=document.getElementById("calendar-search-input"),
              stb=document.getElementById("calendar-search-toggle"),
              scb=document.getElementById("calendar-search-close"),
              sct=document.getElementById("calendar-search-count"),
              ss=document.querySelector('[data-search-scope="calendar"]');
          function norm(v){return(v||"").toString().normalize("NFD").replace(/[̀-ͯ]/g,"").toLowerCase().trim();}
          function applySearch(){
            if(!ss)return;
            var q=norm(si?si.value:""),nodes=ss.querySelectorAll("[data-search]"),total=nodes.length,vis=0;
            nodes.forEach(function(n){var show=!q||norm(n.getAttribute("data-search")||"").indexOf(q)!==-1;n.classList.toggle("search-item-hidden",!show);if(show)vis++;});
            if(sct)sct.textContent=q?(vis+"/"+total):(total+"/"+total);
          }
          function openSearch(f){if(!sc||!stb)return;sc.classList.add("open");stb.setAttribute("aria-expanded","true");if(f&&si){si.focus();si.select();}applySearch();}
          function closeSearch(c){if(!sc||!stb)return;if(c&&si)si.value="";sc.classList.remove("open");stb.setAttribute("aria-expanded","false");applySearch();}
          if(stb)stb.addEventListener("click",function(){sc&&sc.classList.contains("open")?closeSearch(false):openSearch(true);});
          if(scb)scb.addEventListener("click",function(){closeSearch(true);});
          if(si)si.addEventListener("input",applySearch);
          function setSBC(c){var sb=document.getElementById("sidebar");if(!sb)return;sb.classList.toggle("collapsed",c);localStorage.setItem("sidebar_collapsed",c?"1":"0");}
          window.toggleSidebar=function(){var sb=document.getElementById("sidebar");if(!sb)return;setSBC(!sb.classList.contains("collapsed"));};
          window.addEventListener("DOMContentLoaded",function(){
            setSBC(localStorage.getItem("sidebar_collapsed")==="1");
            applySearch();
            if(si&&(si.value||"").trim())openSearch(false);
            var hl=document.getElementById("cal-highlight-appt");
            if(hl)hl.scrollIntoView({behavior:"smooth",block:"center"});
          });
          document.addEventListener("keydown",function(e){if(!(e.ctrlKey||e.metaKey))return;if((e.key||"").toLowerCase()!=="f")return;e.preventDefault();openSearch(true);},true);
        })();
      </script>
    """)
    html.append(f"""<script>
      (function(){{
        var _pills=document.querySelectorAll('.calPill'),
            _panels=document.querySelectorAll('.calViewPanel'),
            _hdr=document.getElementById('cal-date-header'),
            _big=document.getElementById('cal-date-big'),
            _sub=document.getElementById('cal-date-sub'),
            _prev=document.getElementById('cal-nav-prev'),
            _next=document.getElementById('cal-nav-next');
        var _MN=["Enero","Febrero","Marzo","Abril","Mayo","Junio","Julio","Agosto","Septiembre","Octubre","Noviembre","Diciembre"];
        var _DN=["Lunes","Martes","Mi\\u00e9rcoles","Jueves","Viernes","S\\u00e1bado","Domingo"];
        var _calDay=new Date('{today.isoformat()}T12:00:00');

        function _dayName(d){{return _DN[(d.getDay()+6)%7];}}
        function _bigDay(d){{return _MN[d.getMonth()]+' '+d.getDate();}}
        function _iso(d){{return d.getFullYear()+'-'+String(d.getMonth()+1).padStart(2,'0')+'-'+String(d.getDate()).padStart(2,'0');}}

        function updateHeader(view){{
          if(!_big||!_hdr)return;
          if(view==='day'){{_big.textContent=_bigDay(_calDay);_sub.textContent=_dayName(_calDay);}}
          else if(view==='week'){{_big.textContent=_hdr.dataset.weekBig||'';_sub.textContent=_hdr.dataset.weekSub||'';}}
          else{{_big.textContent=_hdr.dataset.monthBig||'';_sub.textContent='';}}
        }}

        function showMonthDay(ds){{
          var det=document.getElementById('cal-month-detail');
          var src=document.getElementById('cal-mday-'+ds);
          if(!det)return;
          det.innerHTML=src?src.innerHTML:'<div style="color:rgba(255,255,255,.4);font-size:13px;padding:8px 0;">Sin turnos.</div>';
          document.querySelectorAll('.calMonthCell').forEach(function(c){{c.classList.remove('selected-cell');}});
          var sel=document.querySelector('.calMonthCell[data-date="'+ds+'"]');
          if(sel)sel.classList.add('selected-cell');
        }}

        function setView(view){{
          _pills.forEach(function(p){{p.classList.remove('active');}});
          _panels.forEach(function(p){{p.classList.remove('active');}});
          var pill=document.querySelector('.calPill[data-view="'+view+'"]');
          if(pill)pill.classList.add('active');
          var panel=document.getElementById('cal-view-'+view);
          if(panel)panel.classList.add('active');
          updateHeader(view);
          history.replaceState(null,'','#'+view);
        }}

        function navGo(dir){{
          var view=(document.querySelector('.calPill.active')||{{}}).getAttribute('data-view')||'day';
          if(view==='day'){{
            _calDay.setDate(_calDay.getDate()+dir);
            updateHeader('day');
            var ds=_iso(_calDay);
            var dp=document.getElementById('cal-view-day');
            if(dp){{
              var src=document.getElementById('cal-dayslots-'+ds);
              dp.innerHTML=src?src.innerHTML:'<div class="calDaySlots"><div style="color:rgba(255,255,255,.35);padding:20px 16px;font-size:13px;">Sin turnos.</div></div>';
            }}
          }}else if(view==='week'){{
            window.location.href=(dir<0?_hdr.dataset.prevWeek:_hdr.dataset.nextWeek)+'#week';
          }}else{{
            window.location.href=(dir<0?_hdr.dataset.prevMonth:_hdr.dataset.nextMonth)+'#month';
          }}
        }}

        _pills.forEach(function(p){{p.addEventListener('click',function(){{setView(p.getAttribute('data-view'));}});}});
        document.querySelectorAll('.calMonthCell[data-date]').forEach(function(c){{c.addEventListener('click',function(){{showMonthDay(c.getAttribute('data-date'));}});}});
        if(_prev)_prev.addEventListener('click',function(){{navGo(-1);}});
        if(_next)_next.addEventListener('click',function(){{navGo(1);}});

        window.addEventListener('DOMContentLoaded',function(){{
          var h=(location.hash||'').replace('#','');
          setView(['day','week','month'].indexOf(h)>=0?h:'day');
          showMonthDay('{today.isoformat()}');
        }});
      }})();
    </script>""")
    html.append("</main></div>")
    return "\n".join(html)


def render_profesionales_page(profesionales: list[Profesional], user_email: str = "") -> str:
    table_css = """
      .tableWrap { overflow: auto; background: rgba(255,255,255,.7); border: 1px solid var(--border); border-radius: 14px; box-shadow: var(--shadow); }
      table { width: 100%; border-collapse: collapse; min-width: 900px; }
      th, td { padding: 8px 10px; border-bottom: 1px solid var(--border); text-align: left; }
      thead th { font-size: 12px; color: #374151; background: #fff; position: sticky; top: 0; z-index: 5; box-shadow: 0 1px 0 rgba(0,0,0,.08); }
      td { font-size: 13px; }
      tr:hover td { background: #f3f4f6; }
      @media (max-width: 768px) { .grid { grid-template-columns: 1fr; } .grid input, .grid select { width: 100%; box-sizing: border-box; } .main { padding: 60px 12px 0 !important; } }
    """
    css = _base_css(extra_css=table_css)

    icon_board = '<svg class="icon" viewBox="0 0 24 24"><rect x="3" y="4" width="7" height="7"/><rect x="14" y="4" width="7" height="7"/><rect x="3" y="15" width="7" height="7"/><rect x="14" y="15" width="7" height="7"/></svg>'
    icon_calendar = '<svg class="icon" viewBox="0 0 24 24"><rect x="3" y="5" width="18" height="16" rx="2"/><path d="M8 3v4M16 3v4M3 11h18"/></svg>'
    icon_filter = '<svg class="icon" viewBox="0 0 24 24"><path d="M4 6h16l-6 7v5l-4 2v-7z"/></svg>'
    icon_prof = '<svg class="icon" viewBox="0 0 24 24"><circle cx="12" cy="7" r="4"/><path d="M4 21c1.5-4 14.5-4 16 0"/></svg>'
    icon_ag = '<svg class="icon" viewBox="0 0 24 24"><path d="M3 21h18"/><path d="M5 21V8l7-5 7 5v13"/><path d="M9 12h6"/></svg>'
    icon_toggle = '<svg class="icon icon-only" viewBox="0 0 24 24"><path d="M15 6l-6 6 6 6"/></svg>'
    build_stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    html: list[str] = [css]
    html.append('<div class="layout">')
    html.append("""
      <aside class="sidebar" id="sidebar">
        <div class="brandRow">
          <div class="brandText">RIDECHECK</div>
          <button class="sidebarToggle" type="button" onclick="toggleSidebar()" title="Collapse sidebar">%s</button>
        </div>
        %s
        %s
      </aside>
    """ % (
        icon_toggle,
        render_sidebar_nav(
            icon_board=icon_board,
            icon_calendar=icon_calendar,
            icon_filter=icon_filter,
            icon_prof=icon_prof,
            icon_ag=icon_ag,
            icon_wa=ICON_WHATSAPP,
        ),
        _sidebar_user_block(user_email),
    ))

    html.append('<main class="main">')
    html.append(f"""
      <div class="kanbanTopBar">
        <div class="kanbanTopBarTitle">Profesionales</div>
        <div class="kanbanTopBarRight">
          <span class="buildStamp">build: {build_stamp}</span>
          <div class="searchControl" id="prof-search-control">
            <button class="iconBtn" id="prof-search-toggle" type="button" title="Buscar (Ctrl+F)" aria-expanded="false">{ICON_SEARCH}</button>
            <div class="searchBoxWrap" id="prof-search-wrap">
              <input id="prof-search-input" class="searchInput" type="text" placeholder="Buscar profesionales..." value=""/>
              <span id="prof-search-count" class="searchCount">0 / 0</span>
              <button class="iconBtn" id="prof-search-close" type="button" title="Cerrar búsqueda">{ICON_CLOSE}</button>
            </div>
          </div>
        </div>
      </div>
    """)

    html.append("""
      <div class="box" style="max-width: 720px;">
        <div class="menuTitle">Agregar profesional</div>
        <form method="post" action="/ui/profesional_create" style="margin-top:10px;">
          <div class="grid">
            <div>
              <div class="label">Nombre</div>
              <input name="nombre" required />
            </div>
            <div>
              <div class="label">Apellido</div>
              <input name="apellido" required />
            </div>
          </div>
          <div class="grid" style="margin-top:8px;">
            <div>
              <div class="label">Email</div>
              <input name="email" type="email" required />
            </div>
            <div>
              <div class="label">Teléfono</div>
              <input name="telefono" />
            </div>
          </div>
          <div class="grid" style="margin-top:8px;">
            <div>
              <div class="label">Cargo</div>
              <input name="cargo" />
            </div>
          </div>
          <div class="stack" style="margin-top:10px;">
            <button class="btn btn-primary" type="submit">Crear</button>
          </div>
        </form>
      </div>
    """)

    rows: list[str] = []
    for p in profesionales:
        created_txt = p.created_at.strftime("%d/%m %H:%M") if p.created_at else "-"
        rows.append(f"""
          <tr>
            <td>{_txt(p.nombre)}</td>
            <td>{_txt(p.apellido)}</td>
            <td>{_txt(p.email)}</td>
            <td>{_txt(getattr(p, "telefono", None))}</td>
            <td>{_txt(p.cargo)}</td>
            <td>{created_txt}</td>
          </tr>
        """)

    html.append("""
      <div class="tableWrap" style="margin-top:14px;" data-search-scope="prof">
        <table>
          <thead>
            <tr>
              <th>Nombre</th>
              <th>Apellido</th>
              <th>Email</th>
              <th>Teléfono</th>
              <th>Cargo</th>
              <th>Creado</th>
            </tr>
          </thead>
          <tbody>
            %s
          </tbody>
        </table>
      </div>
    """ % "\n".join(rows))

    html.append("""
      <script>
        (function () {
          var searchControl = document.getElementById("prof-search-control");
          var searchInput = document.getElementById("prof-search-input");
          var searchToggleBtn = document.getElementById("prof-search-toggle");
          var searchCloseBtn = document.getElementById("prof-search-close");
          var searchCount = document.getElementById("prof-search-count");
          var searchScope = document.querySelector('[data-search-scope="prof"]');
          function normalizeSearchText(value) {
            return (value || "")
              .toString()
              .normalize("NFD")
              .replace(/[\\u0300-\\u036f]/g, "")
              .toLowerCase()
              .trim();
          }
          function applyProfSearch() {
            if (!searchScope) return;
            var q = normalizeSearchText(searchInput ? searchInput.value : "");
            var dataTargets = searchScope.querySelectorAll("[data-search]");
            var total = 0;
            var visible = 0;
            if (dataTargets.length) {
              dataTargets.forEach(function (node) {
                total += 1;
                var haystack = normalizeSearchText(node.getAttribute("data-search") || "");
                var show = !q || haystack.indexOf(q) !== -1;
                node.classList.toggle("search-item-hidden", !show);
                if (show) visible += 1;
              });
            } else {
              var rows = searchScope.querySelectorAll("tbody tr");
              rows.forEach(function (row) {
                total += 1;
                var haystack = normalizeSearchText(row.textContent || "");
                var show = !q || haystack.indexOf(q) !== -1;
                row.style.display = show ? "" : "none";
                if (show) visible += 1;
              });
            }
            if (searchCount) {
              searchCount.textContent = q ? (visible + " / " + total) : (total + " / " + total);
            }
          }
          function openProfSearch(focusInput) {
            if (!searchControl || !searchToggleBtn) return;
            searchControl.classList.add("open");
            searchToggleBtn.setAttribute("aria-expanded", "true");
            if (focusInput && searchInput) {
              searchInput.focus();
              searchInput.select();
            }
            applyProfSearch();
          }
          function closeProfSearch(clearValue) {
            if (!searchControl || !searchToggleBtn) return;
            if (clearValue && searchInput) searchInput.value = "";
            searchControl.classList.remove("open");
            searchToggleBtn.setAttribute("aria-expanded", "false");
            applyProfSearch();
          }
          if (searchToggleBtn) {
            searchToggleBtn.addEventListener("click", function () {
              var isOpen = searchControl && searchControl.classList.contains("open");
              if (isOpen) {
                closeProfSearch(false);
                return;
              }
              openProfSearch(true);
            });
          }
          if (searchCloseBtn) {
            searchCloseBtn.addEventListener("click", function () {
              closeProfSearch(true);
            });
          }
          if (searchInput) {
            searchInput.addEventListener("input", applyProfSearch);
          }
          function setSidebarCollapsed(collapsed) {
            var sb = document.getElementById("sidebar");
            if (!sb) return;
            sb.classList.toggle("collapsed", collapsed);
            localStorage.setItem("sidebar_collapsed", collapsed ? "1" : "0");
          }
          window.toggleSidebar = function () {
            var sb = document.getElementById("sidebar");
            if (!sb) return;
            var collapsed = sb.classList.contains("collapsed");
            setSidebarCollapsed(!collapsed);
          };
          window.addEventListener("DOMContentLoaded", function () {
            var sbCollapsed = localStorage.getItem("sidebar_collapsed") === "1";
            setSidebarCollapsed(sbCollapsed);
            applyProfSearch();
            if (searchInput && (searchInput.value || "").trim()) {
              openProfSearch(false);
            }
          });
          document.addEventListener("keydown", function (e) {
            if (!(e.ctrlKey || e.metaKey)) return;
            if ((e.key || "").toLowerCase() !== "f") return;
            e.preventDefault();
            openProfSearch(true);
          }, true);
        })();
      </script>
    """)
    html.append("</main></div>")
    return "\n".join(html)


def render_lead_card(
    l: Lead,
    zones_map: dict[str, list[str]] | None = None,
    profesionales: list[Profesional] | None = None,
    agencias: list[Agencia] | None = None,
) -> str:
    n = f"{(_get(l,'nombre') or '').strip()} {(_get(l,'apellido') or '').strip()}".strip() or "-"
    tel = _txt(_get(l, "telefono"))
    email = _txt(_get(l, "email"))

    revs = sorted(list(_get(l, "revisions") or []), key=lambda r: r.created_at, reverse=True)
    last_rev = revs[0] if revs else None
    rev_count = len(revs)
    total_vals = [r.precio_total for r in revs if r.precio_total is not None]
    total_presu_txt = _fmt_money(sum(total_vals)) if total_vals else "-"

    prof_by_id = {p.id: p for p in (profesionales or [])}
    last_prof = None
    if last_rev:
        last_prof = getattr(last_rev, "profesional", None)
        if not last_prof:
            pid = getattr(last_rev, "profesional_id", None)
            last_prof = prof_by_id.get(pid) if pid else None
    last_prof_label = _profesional_label(last_prof) if last_prof else "-"

    vehicle_badge = "Vehículo"
    if last_rev and last_rev.tipo_vehiculo:
        vehicle_badge = _friendly_tipo_vehiculo(last_rev.tipo_vehiculo)

    base_cls = "card leadCard"
    if bool(_get(l, "necesita_humano")):
        base_cls += " humanAlert"
    card_cls = base_cls
    created_at = _get(l, "created_at")
    created_txt = created_at.strftime("%d/%m %H:%M") if created_at else ""
    flag_val = _lead_flag_value(l)
    flag_label = FLAG_LABELS.get(flag_val, flag_val) if flag_val else None
    current_estado = _lead_operational_estado(_get(l, "estado"))
    status_label = KANBAN_LABELS.get(current_estado, current_estado)
    status_cls = "leadStatus status-default"
    status_locked = "0"
    header_flag_txt = _txt(flag_label) if flag_label else "Sin flag"
    header_flag_html = f'<span class="headerFlag">{header_flag_txt}</span>'

    prof_name_search = ""
    if last_prof:
        prof_name_search = f"{(last_prof.nombre or '').strip()} {(last_prof.apellido or '').strip()}".strip()
    vehicle_search = ""
    vehicle_bits = [
        _val(last_rev.marca) if last_rev else "",
        _val(last_rev.modelo) if last_rev else "",
        str(last_rev.anio) if (last_rev and last_rev.anio) else "",
    ]
    vehicle_bits = [x for x in vehicle_bits if x]
    if vehicle_bits:
        vehicle_search = " ".join(vehicle_bits)
    search_text = " ".join(
        x for x in [
            f"{_val(_get(l, 'nombre'))} {_val(_get(l, 'apellido'))}".strip(),
            vehicle_search,
            prof_name_search,
        ] if x
    )
    search_attr = html_lib.escape(search_text, quote=True)

    # Attention icon (top-left)
    human_on = bool(_get(l, "necesita_humano"))
    toggle_to = "false" if human_on else "true"
    human_icon_user = '<svg class="icon icon-only" viewBox="0 0 24 24"><circle cx="12" cy="8" r="4"/><path d="M4 20c1.5-4 14.5-4 16 0"/></svg>'
    human_icon_alert = '<svg class="icon icon-only" viewBox="0 0 24 24"><path d="M12 4l9 16H3z"/><path d="M12 9v4M12 17h.01"/></svg>'
    human_icon = human_icon_user if not human_on else human_icon_alert

    # 3-dots MENU (top-right): edit lead + perdido + delete
    canal_val = _get(l, "canal") if _has(l, "canal") else None
    compro_val = _get(l, "compro_el_auto") if _has(l, "compro_el_auto") else None

    canal_options_html = "".join(
        f'<option value="{c}" {"selected" if canal_val == c else ""}>{c}</option>'
        for c in CANAL_OPCIONES
    )

    compro_options_html = f"""
      <option value="">-</option>
      <option value="SI" {"selected" if compro_val == "SI" else ""}>SI</option>
      <option value="NO" {"selected" if compro_val == "NO" else ""}>NO</option>
    """
    estado_options_html = "".join(
        f'<option value="{s}" {"selected" if current_estado == s else ""}>{KANBAN_LABELS.get(s, s)}</option>'
        for s in KANBAN_ORDER
    )
    humano_options_html = f"""
      <option value="">-</option>
      <option value="true" {"selected" if bool(_get(l, "necesita_humano")) else ""}>SI</option>
      <option value="false" {"selected" if not bool(_get(l, "necesita_humano")) else ""}>NO</option>
    """
    show_perdido_action = flag_val != "RECOMPRA"

    icon_tag = '<svg class="icon" viewBox="0 0 24 24"><path d="M20 13l-7 7-10-10V3h7l10 10z"/><circle cx="7.5" cy="7.5" r="1.5"/></svg>'
    icon_broom = '<svg class="icon" viewBox="0 0 24 24"><path d="M3 13l9 9 3-3-9-9H3z"/><path d="M14 3l7 7"/><path d="M10 7l7 7"/></svg>'
    icon_edit = '<svg class="icon" viewBox="0 0 24 24"><path d="M3 21l3-1 11-11-2-2L4 18l-1 3z"/><path d="M14 4l2 2"/></svg>'
    icon_trash = '<svg class="icon" viewBox="0 0 24 24"><path d="M3 6h18M8 6v-2h8v2M9 10v8M15 10v8M6 6l1 14h10l1-14"/></svg>'
    icon_alert = '<svg class="icon" viewBox="0 0 24 24"><path d="M12 4l9 16H3z"/><path d="M12 9v4M12 17h.01"/></svg>'

    perdido_action_html = ""
    perdido_inline_html = ""
    if show_perdido_action:
        perdido_action_html = f"""
          <button class="btn" type="button" onclick="openPerdidoInline({l.id}, this)">{icon_tag}{FLAG_LABELS.get("PERDIDO", "PERDIDO")}</button>
        """
        perdido_inline_html = f"""
          <div class="menuInline" id="perdido-inline-{l.id}" style="display:none;">
            <form method="post" action="/ui/perdido">
              <input type="hidden" name="lead_id" value="{l.id}"/>
              <div class="menuTitle">Motivo de pérdida</div>
              <select name="motivo_perdida">
                <option value="PRECIO">Perdido por precio</option>
                <option value="DISPONIBILIDAD">Perdido por disponibilidad</option>
                <option value="OTRO">Perdido por otro</option>
              </select>
              <div class="menuInlineActions">
                <button class="btn btn-danger" type="submit">Confirmar</button>
                <button class="btn" type="button" onclick="closePerdidoInline({l.id})">Cancelar</button>
              </div>
            </form>
          </div>
        """

    lead_edit_modal_html = f"""
      <div id="editlead-{l.id}" class="revModalOverlay" data-lead-edit-modal-for="{l.id}">
        <div class="revModal" role="dialog" aria-modal="true" aria-label="Editar lead">
          <div class="revModalHead">
            <div class="revModalTitle">Editar lead</div>
            <button class="iconBtn" type="button" aria-label="Cerrar" onclick="closeLeadEditModal({l.id})">{ICON_CLOSE}</button>
          </div>
          <form method="post" action="/ui/lead_update" class="revEditPanel">
            <input type="hidden" name="lead_id" value="{l.id}"/>
            <div class="revModalBody">
              <div class="grid">
                <div>
                  <div class="label">Nombre</div>
                  <input name="nombre" value="{_val(_get(l,'nombre'))}"/>
                </div>
                <div>
                  <div class="label">Apellido</div>
                  <input name="apellido" value="{_val(_get(l,'apellido'))}"/>
                </div>
              </div>

              <div class="grid" style="margin-top:8px;">
                <div>
                  <div class="label">Teléfono</div>
                  <input name="telefono" value="{_val(_get(l,'telefono'))}"/>
                </div>
                <div>
                  <div class="label">Email</div>
                  <input name="email" value="{_val(_get(l,'email'))}"/>
                </div>
              </div>

              <div class="grid" style="margin-top:8px;">
                <div>
                  <div class="label">Canal</div>
                  <select name="canal">
                    <option value="">-</option>
                    {canal_options_html}
                  </select>
                </div>
                <div>
                  <div class="label">Compró el auto</div>
                  <select name="compro_el_auto">
                    {compro_options_html}
                  </select>
                </div>
              </div>

              <div class="grid" style="margin-top:8px;">
                <div>
                  <div class="label">Lead estado</div>
                  <select name="estado">
                    {estado_options_html}
                  </select>
                </div>
                <div>
                  <div class="label">Necesita humano</div>
                  <select name="necesita_humano">
                    {humano_options_html}
                  </select>
                </div>
              </div>
            </div>
            <div class="revModalFooter">
              <button class="btn btn-primary" type="submit">Guardar lead</button>
              <button class="btn" type="button" onclick="closeLeadEditModal({l.id})">Cancelar</button>
            </div>
          </form>
        </div>
      </div>
    """

    # quick summary
    rev_lines: list[str] = []
    if last_rev:
        veh_line = " / ".join([x for x in [
            _val(last_rev.marca),
            _val(last_rev.modelo),
            str(last_rev.anio) if last_rev.anio else "",
        ] if x])
        if veh_line:
            rev_lines.append(f"Vehículo: {_txt(veh_line)}")

        if last_rev.precio_total is not None or last_rev.precio_base is not None or last_rev.viaticos is not None:
            rev_lines.append(
                f"Presupuesto: Base {_fmt_money(last_rev.precio_base)} + Viáticos {_fmt_money(last_rev.viaticos)} = {_fmt_money(last_rev.precio_total)}"
            )

        approval_tag = _revision_approval_tag(last_rev)
        if approval_tag:
            rev_lines.append(f"Aprobacion turno: {approval_tag}")

    # revisions block
    revisions_block = render_revisions_block(l, revs, last_rev, zones_map, profesionales or [], agencias or [])

    header_cls = "cardHeaderRow"
    if flag_val:
        header_cls = f"cardHeaderRow flag-{flag_val}"
    vehicle_pill_html = ""
    if vehicle_bits:
        vehicle_pill_html = f'<div class="leadVehicleRow"><span class="pill pill-gray">{" / ".join(vehicle_bits)}</span></div>'

    return f"""
        <div class="{card_cls}" id="lead-{l.id}" data-lead-id="{l.id}" data-current-estado="{current_estado}" data-search="{search_attr}">
          <div class="{header_cls} card-header" draggable="true" data-drag-handle="1">
            <div class="cardHeaderTop lead-head">
              <div class="cardHeaderTopLeft lead-head-left">
                <form method="post" action="/ui/human">
                  <input type="hidden" name="lead_id" value="{l.id}"/>
                  <input type="hidden" name="necesita_humano" value="{toggle_to}"/>
                  <button class="leadIdBadge" title="Atención humana" type="submit">{human_icon}<span>{l.id}</span></button>
                </form>
                <button class="leadWaBtn waIconBtn" type="button" title="WhatsApp" data-lead-wa-btn="1" data-lead-id="{l.id}">{ICON_WHATSAPP}</button>
              </div>
              <div class="cardHeaderRight card-header-right lead-head-right">
                {header_flag_html}
                <details class="menu">
                  <summary class="iconBtn" title="Acciones">{ICON_ELLIPSIS}</summary>
                  <div class="menuPanel">
                    <div class="menuMainActions" id="menu-main-{l.id}">
                    <div class="menuTitle">Acciones lead</div>

                <div class="menuEstadoQuick">
                  <div class="label">Estado</div>
                  <select data-quick-estado="1" data-lead-id="{l.id}">
                    {estado_options_html}
                  </select>
                </div>

                  <div class="divider"></div>

                <form method="post" action="/ui/lead_toggle_humano">
                  <input type="hidden" name="lead_id" value="{l.id}"/>
                  <input type="hidden" name="value" value="{("0" if human_on else "1")}"/>
                  <button class="btn" type="submit">{icon_alert}{("Desactivar intervención humana" if human_on else "Intervención humana")}</button>
                </form>

                  <div class="divider"></div>

                  <div class="menuTitle">Lead flags</div>
                  <div class="stack" style="margin-top:6px;">
                    {''.join(
                        f'''
                    <form method="post" action="/ui/lead_flag_set">
                      <input type="hidden" name="lead_id" value="{l.id}"/>
                      <input type="hidden" name="flag" value="{fv}"/>
                      <button class="btn" type="submit">{icon_tag}{FLAG_LABELS.get(fv, fv)}</button>
                    </form>
                        ''' for fv in [x for x in FLAG_VALUES if x != "PERDIDO"]
                    )}
                    {perdido_action_html}
                    <form method="post" action="/ui/lead_flag_clear">
                      <input type="hidden" name="lead_id" value="{l.id}"/>
                      <button class="btn" type="submit">{icon_broom}Limpiar flag</button>
                    </form>
                  </div>

                  <div class="divider"></div>

                  <button class="btn" type="button" onclick="openLeadEditModal({l.id}, this)">{icon_edit}Editar lead</button>

                <div class="divider"></div>

                <button class="btn btn-danger" type="button" onclick="requestDeleteLead({l.id}, this)">{icon_trash}Eliminar lead</button>
                <div class="danger-note" style="margin-top:6px;">
                  Se puede deshacer por 7 segundos.
                </div>
                </div>
                {perdido_inline_html}
              </div>
                </details>
              </div>
            </div>
            <div class="cardHeaderBottom">
              <span class="pill pill-veh">{vehicle_badge}</span>
              <span class="pill pill-prof">Profesional: {_txt(last_prof_label)}</span>
              {(_render_revision_approval_ui(last_rev, lead_id=l.id) if last_rev else "")}
            </div>
          </div>

          {vehicle_pill_html}

        <div class="leadNameRow">
          <button class="leadToggle" type="button" aria-expanded="false" onclick="toggleLeadDetails({l.id}, this)">
            <span>{n}</span>
            <span class="leadCaret">{ICON_CHEVRON_DOWN}</span>
          </button>
        </div>

        <div class="leadDetailsBody" id="lead-details-{l.id}">
          <div class="muted leadContact">Tel: {tel} · Email: {email}</div>
          <div class="leadRevPanel">
            <div class="leadRevTotal">Total presupuestado: {total_presu_txt}</div>
            <div class="leadRevLines">
              {(
                f'<div>Motivo pérdida: {_txt(_get(l, "motivo_perdida"))}</div>'
                if _get(l, "motivo_perdida")
                else ""
              )}
              {''.join(f"<div>{x}</div>" for x in rev_lines)}
              <div>Estado operativo: {_txt(last_rev.estado_revision) if last_rev else "-"}</div>
              {(_render_revision_approval_ui(last_rev, lead_id=l.id) if last_rev else "")}
            </div>
          </div>
        </div>

        {lead_edit_modal_html}
        {revisions_block}
      </div>
    """


def render_revisions_block(
    l: Lead,
    revs: list[Revision],
    last_rev: Revision | None,
    zones_map: dict[str, list[str]] | None = None,
    profesionales: list[Profesional] | None = None,
    agencias: list[Agencia] | None = None,
) -> str:
    rev_count = len(revs)
    revs_chrono = sorted(
        list(revs or []),
        key=lambda r: (
            r.created_at or datetime.min,
            r.id or 0,
        ),
    )
    revs_display = sorted(
        list(revs or []),
        key=lambda r: (
            r.created_at or datetime.min,
            r.id or 0,
        ),
        reverse=True,
    )
    rev_num_by_id = {r.id: i + 1 for i, r in enumerate(revs_chrono)}
    prof_by_id = {p.id: p for p in (profesionales or [])}
    icon_plus = '<svg class="icon" viewBox="0 0 24 24"><path d="M12 5v14M5 12h14"/></svg>'
    icon_edit = '<svg class="icon" viewBox="0 0 24 24"><path d="M3 21l3-1 11-11-2-2L4 18l-1 3z"/><path d="M14 4l2 2"/></svg>'
    icon_trash = '<svg class="icon" viewBox="0 0 24 24"><path d="M3 6h18M8 6v-2h8v2M9 10v8M15 10v8M6 6l1 14h10l1-14"/></svg>'

    chunks: list[str] = []
    chunks.append(f"""
      <details class="box revBox" id="revs-{l.id}">
        <div class="revMenu">
          <details class="menu">
            <summary class="iconBtn" title="Acciones">{ICON_ELLIPSIS}</summary>

            <div class="menuPanel">
              <div class="menuTitle">Acciones revisiones</div>

              <form method="post" action="/ui/revision_create" data-rev-create="1">
                <input type="hidden" name="lead_id" value="{l.id}"/>
                <button class="btn btn-primary" type="submit">{icon_plus}Nueva revisión</button>
              </form>

              <div class="divider"></div>

              {(
                f'<button class="btn" type="button" onclick="openEditLatest({l.id})">{icon_edit}Editar última revisión</button>'
                if last_rev
                else '<div class="muted small">No hay revisiones para editar.</div>'
              )}

              <div class="divider"></div>

              {(
                f'<button class="btn btn-danger" type="button" onclick="requestDeleteLatestRevision({l.id})">'
                f'{icon_trash}Borrar última revisión</button>'
                if last_rev
                else '<div class="muted small">No hay revisiones para borrar.</div>'
              )}
            </div>
          </details>
        </div>
        <summary class="revSummary">
          <span id="rev-count-{l.id}" data-rev-count="{rev_count}">Ver revisiones ({rev_count})</span>
        </summary>
    """)

    if not revs:
        chunks.append('<div class="muted" style="margin-top:10px;">No hay revisiones.</div>')
    else:
        for r in revs_display:
            rev_num = rev_num_by_id.get(r.id, 0)
            turno_txt = "-"
            if r.turno_fecha or r.turno_hora:
                tf = r.turno_fecha.strftime("%d/%m/%Y") if r.turno_fecha else "-"
                th = r.turno_hora.strftime("%H:%M") if r.turno_hora else "-"
                turno_txt = f"{tf} {th}"

            presu_txt = f"{_fmt_money(r.precio_base)} + {_fmt_money(r.viaticos)} = {_fmt_money(r.precio_total)}"
            prof = getattr(r, "profesional", None)
            if not prof:
                pid = getattr(r, "profesional_id", None)
                prof = prof_by_id.get(pid) if pid else None
            prof_label = _profesional_label(prof) if prof else "-"

            tipo_vendedor_txt = _txt(getattr(r, "tipo_vendedor", None) or r.vendedor_tipo)
            agencia_txt = "-"
            if getattr(r, "agencia", None):
                agencia_txt = _txt(getattr(r.agencia, "nombre_agencia", None))
            elif getattr(r, "agencia_id", None):
                agencia_txt = str(getattr(r, "agencia_id"))
            approval_status = _val(getattr(r, "appointment_approval_status", None)).upper()
            approval_html = ""
            if approval_status == "PENDING":
                approval_html = f"""
                  <div class="revApprovalRow">
                    <button class="btn btn-sm btn-primary" type="button" onclick="updateAppointmentApproval({r.id}, 'APPROVED')">✓ Confirmar turno</button>
                    <button class="btn btn-sm btn-danger" type="button" onclick="updateAppointmentApproval({r.id}, 'REJECTED')">✗ Rechazar turno</button>
                  </div>
                """
            elif approval_status == "APPROVED":
                approval_html = '<div class="revApprovalRow"><span class="pill pill-prof">✓ Turno confirmado</span></div>'

            approval_html = _render_revision_approval_ui(r, include_actions=True, lead_id=l.id)
            chunks.append(f"""
              <div class="rev" id="rev-{l.id}-{r.id}">
                <div class="revHead">
                  <div class="revHeadLine1">
                    <span class="revHeadTitle">Revisión {rev_num}</span>
                    <span class="revHeadTurno">Turno: {_txt(turno_txt)}</span>
                  </div>
                  <div class="revHeadLine2">
                    <span class="pill pill-prof">Profesional: {_txt(prof_label)}</span>
                  </div>
                  <div class="revHeadLine3">
                    <span class="pill revEstadoPill">Estado: {_txt(r.estado_revision)}</span>
                  </div>
                </div>

                <div class="box">
                  <div class="small"><b>Vehículo</b></div>
                  <div class="muted small">Tipo: {_txt(r.tipo_vehiculo)} | Marca: {_txt(r.marca)} | Modelo: {_txt(r.modelo)} | Año: {str(r.anio) if r.anio else "-"}</div>
                  <div class="muted small">Compra: {_url_link(r.link_compra)} | Presu compra: {_fmt_money(r.presupuesto_compra)} | Tipo vendedor: {tipo_vendedor_txt} | Agencia: {agencia_txt}</div>
                  <div class="muted small">Compró: {_txt(getattr(r, "compro", None))} | Comisión: {_fmt_money(getattr(r, "comision", None))} | Cobrado: {_txt(getattr(r, "cobrado", None))} | Fecha cobro: {_txt(getattr(r, "fecha_cobro", None))}</div>
                </div>

                <div class="box">
                  <div class="small"><b>Zona / Dirección</b></div>
                  <div class="muted small">Zona: {_txt(r.zone_group)} / {_txt(r.zone_detail)}</div>
                  <div class="muted small">Dirección: {_txt(r.direccion_texto)}</div>
                  <div class="muted small">Maps: {_url_link(r.link_maps)}</div>
                </div>

                <div class="box">
                  <div class="small"><b>Presupuesto / Pago</b></div>
                  <div class="muted small">Presupuesto: {presu_txt}</div>
                  <div class="muted small">Pago: {("SI" if r.pago else ("NO" if r.pago is False else "-"))} | Medio: {_txt(r.medio_pago)}</div>
                </div>

                <div class="box">
                  <div class="small"><b>Turno</b></div>
                  <div class="muted small">Inicio: {turno_txt}</div>
                  <div class="muted small">Cliente presente: {("SI" if r.cliente_presente else ("NO" if r.cliente_presente is False else "-"))}</div>
                  <div class="muted small">Notas: {_txt(r.turno_notas)}</div>
                  {approval_html}
                </div>

                <div class="box">
                  <div class="small"><b>Resultado</b></div>
                  <div class="muted small">Resultado: {_txt(r.resultado)} | Motivo rechazo: {_txt(r.motivo_rechazo)} | Link técnico: {_url_link(getattr(r, "resultado_link", None))}</div>
                </div>
              </div>
            """)

    # edit latest revision form is still accessible, but controlled via latest menu
    if last_rev:
        last_rev_num = rev_num_by_id.get(last_rev.id, 0)
        chunks.append(
            render_edit_latest_revision_form(
                l.id,
                last_rev,
                last_rev_num,
                zones_map,
                profesionales or [],
                agencias or [],
            )
        )

    chunks.append("</details>")
    return "\n".join(chunks)


def _latest_rev_menu_html(lead_id: int, last_rev: Revision | None) -> str:
    if not last_rev:
        return ""
    icon_trash = '<svg class="icon" viewBox="0 0 24 24"><path d="M3 6h18M8 6v-2h8v2M9 10v8M15 10v8M6 6l1 14h10l1-14"/></svg>'
    return f"""
      <details class="menu">
        <summary class="btn">{ICON_ELLIPSIS}</summary>
        <div class="menuPanel">
          <div class="menuTitle">Última revisión (Revisión {last_rev.id})</div>
          <div class="muted small" style="margin-bottom:10px;">Borrar la Última revisión.</div>

          <form method="post" action="/ui/revision_latest_delete">
            <input type="hidden" name="lead_id" value="{lead_id}"/>
            <button class="btn btn-danger" type="submit"
              onclick="return confirm('-Borrar la Última revisión del lead #{lead_id}?');">
              {icon_trash}Borrar última revisión
            </button>
          </form>
        </div>
      </details>
    """


def render_edit_latest_revision_form(
    lead_id: int,
    last_rev: Revision,
    last_rev_num: int,
    zones_map: dict[str, list[str]] | None = None,
    profesionales: list[Profesional] | None = None,
    agencias: list[Agencia] | None = None,
) -> str:
    tf_val = last_rev.turno_fecha.isoformat() if last_rev.turno_fecha else ""
    th_val = last_rev.turno_hora.strftime("%H:%M") if last_rev.turno_hora else ""

    def opt(selected: str | None, val: str) -> str:
        return f'<option value="{val}" {"selected" if selected == val else ""}>{val}</option>'

    # If in the future you add Revision.informe_pdf, this won-t break:
    has_pdf = hasattr(last_rev, "informe_pdf")

    zones_map = zones_map or {}
    has_zones = bool(zones_map)
    zone_groups = sorted(zones_map.keys()) if has_zones else []
    zone_group_val = _val(last_rev.zone_group)
    zone_detail_val = _val(last_rev.zone_detail)

    profesionales = profesionales or []
    profesional_options = "".join(
        f'<option value="{p.id}" {"selected" if last_rev.profesional_id == p.id else ""}>{_profesional_label(p)}</option>'
        for p in profesionales
    )
    agencias = agencias or []
    selected_tipo_vendedor = _val(getattr(last_rev, "tipo_vendedor", None) or last_rev.vendedor_tipo)
    selected_agencia_id = _val(getattr(last_rev, "agencia_id", None))
    agencia_options = "".join(
        f'<option value="{a.id}" {"selected" if selected_agencia_id == str(a.id) else ""}>{_txt(a.nombre_agencia)}</option>'
        for a in agencias
    )

    if has_zones:
        zone_group_options = "".join(
            f'<option value="{g}" {"selected" if g == zone_group_val else ""}>{g}</option>'
            for g in zone_groups
        )
        zone_detail_options = "".join(
            f'<option value="{d}" {"selected" if d == zone_detail_val else ""}>{d}</option>'
            for d in (zones_map.get(zone_group_val) or [])
        )
        zone_inputs_html = f"""
          <div class="grid" style="margin-top:8px;">
            <div>
              <div class="label">Zona grupo</div>
              <select name="zone_group" data-zone-group="1">
                <option value="">-</option>
                {zone_group_options}
              </select>
            </div>
            <div>
              <div class="label">Zona detalle</div>
              <select name="zone_detail" data-zone-detail="1">
                <option value="">-</option>
                {zone_detail_options}
              </select>
            </div>
          </div>
        """
    else:
        zone_inputs_html = f"""
          <div class="grid" style="margin-top:8px;">
            <div>
              <div class="label">Zona grupo</div>
              <input name="zone_group" value="{zone_group_val}"/>
            </div>
            <div>
              <div class="label">Zona detalle</div>
              <input name="zone_detail" value="{zone_detail_val}"/>
            </div>
          </div>
        """

    return f"""
      <div id="editrev-{lead_id}" class="revModalOverlay" data-rev-modal-for="{lead_id}">
        <div class="revModal" role="dialog" aria-modal="true" aria-label="Editar revisión">
          <div class="revModalHead">
            <div class="revModalTitle">Editar revisión {last_rev_num}</div>
            <button class="iconBtn" type="button" aria-label="Cerrar" onclick="closeEditLatest({lead_id})">{ICON_CLOSE}</button>
          </div>

          <form method="post" action="/ui/revision_latest_update" class="revEditPanel" data-revision-id="{last_rev.id}" data-current-turno-time="{th_val}">
            <input type="hidden" name="lead_id" value="{lead_id}"/>
            <div class="revModalBody">
              <style>
                #editrev-{lead_id} .revSection {{ margin-bottom:12px; }}
                #editrev-{lead_id} .revSection > legend {{ font-size:13px; font-weight:800; color:#111827; padding:0 4px; }}
                #editrev-{lead_id} .revSectionBody {{ margin-top:8px; }}
                #editrev-{lead_id} .revReadonly {{ background:#f3f4f6; color:#4b5563; }}
                #editrev-{lead_id} .revHint {{ margin-top:6px; }}
                #editrev-{lead_id} .revScheduleSuggestions {{ display:flex; flex-wrap:wrap; gap:6px; margin-top:6px; }}
                #editrev-{lead_id} .revScheduleChip {{ border:1px solid #fcd34d; background:#fffbeb; color:#92400e; border-radius:999px; padding:4px 10px; font-size:12px; font-weight:700; cursor:pointer; }}
                #editrev-{lead_id} .revScheduleChip:hover {{ background:#fef3c7; }}
              </style>

              <fieldset class="box revSection">
                <legend>Vehículo</legend>
                <div class="revSectionBody">
                  <div class="grid">
                    <div>
                      <div class="label">Tipo vehículo</div>
                      <select name="tipo_vehiculo">
                        <option value="">-</option>
                        {''.join(opt(last_rev.tipo_vehiculo, t) for t in TIPOS_VEHICULO)}
                      </select>
                    </div>
                    <div>
                      <div class="label">Profesional</div>
                      <select name="profesional_id">
                        <option value="">-</option>
                        {profesional_options}
                      </select>
                    </div>
                  </div>

                  <div class="grid" style="margin-top:8px;">
                    <div>
                      <div class="label">Marca</div>
                      <input name="marca" value="{_val(last_rev.marca)}"/>
                    </div>
                    <div>
                      <div class="label">Modelo</div>
                      <input name="modelo" value="{_val(last_rev.modelo)}"/>
                    </div>
                  </div>

                  <div class="grid" style="margin-top:8px;">
                    <div>
                      <div class="label">Año</div>
                      <input name="anio" type="number" value="{last_rev.anio or ''}"/>
                    </div>
                    <div>
                      <div class="label">Presupuesto compra</div>
                      <input name="presupuesto_compra" type="number" value="{last_rev.presupuesto_compra or ''}"/>
                    </div>
                  </div>

                  <div class="grid-1" style="margin-top:8px;">
                    <div>
                      <div class="label">Link compra</div>
                      <input name="link_compra" value="{_val(last_rev.link_compra)}"/>
                    </div>
                  </div>

                  <div class="grid" style="margin-top:8px;">
                    <div>
                      <div class="label">Tipo de vendedor</div>
                      <select name="tipo_vendedor" data-tipo-vendedor="1">
                        <option value="">-</option>
                        {''.join(opt(selected_tipo_vendedor, t) for t in VENDEDOR_TIPOS)}
                      </select>
                    </div>
                    <div>
                      <div class="label">Agencia</div>
                      <select name="agencia_id" data-agencia-select="1">
                        <option value="">-</option>
                        {agencia_options}
                      </select>
                    </div>
                  </div>

                  <div class="grid" style="margin-top:8px; {'display:none;' if selected_tipo_vendedor != 'AGENCIA' else ''}" data-agencia-wrap="1">
                    <div>
                      <div class="label">Nueva agencia (rápido)</div>
                      <input name="agencia_nueva_nombre" placeholder="Nombre de agencia"/>
                    </div>
                  </div>

                  <div class="grid" style="margin-top:8px;">
                    <div>
                      <div class="label">Compró</div>
                      <select name="compro">
                        <option value="">-</option>
                        {''.join(opt(getattr(last_rev, 'compro', None), c) for c in REVISION_COMPRO_OPCIONES)}
                      </select>
                    </div>
                    <div>
                      <div class="label">Comisión</div>
                      <input name="comision" type="number" value="{getattr(last_rev, 'comision', None) or ''}"/>
                    </div>
                  </div>

                  <div class="grid" style="margin-top:8px;">
                    <div>
                      <div class="label">Cobrado</div>
                      <select name="cobrado">
                        <option value="">-</option>
                        <option value="SI" {"selected" if getattr(last_rev, 'cobrado', None) == 'SI' else ""}>SI</option>
                        <option value="NO" {"selected" if getattr(last_rev, 'cobrado', None) == 'NO' else ""}>NO</option>
                      </select>
                    </div>
                    <div>
                      <div class="label">Fecha cobro</div>
                      <input type="date" name="fecha_cobro" value="{getattr(last_rev, 'fecha_cobro', None).isoformat() if getattr(last_rev, 'fecha_cobro', None) else ''}"/>
                    </div>
                  </div>

                  <div class="grid-1" style="margin-top:8px;">
                    <div>
                      <div class="label">Resultado técnico (link/doc)</div>
                      <input name="resultado_link" value="{_val(getattr(last_rev, 'resultado_link', None))}"/>
                    </div>
                  </div>
                </div>
              </fieldset>

              <fieldset class="box revSection">
                <legend>Zona / Dirección</legend>
                <div class="revSectionBody">
                  {zone_inputs_html}
                  <div class="grid-1" style="margin-top:8px;">
                    <div>
                      <div class="label">Dirección</div>
                      <input name="direccion_texto" value="{_val(last_rev.direccion_texto)}"/>
                    </div>
                    <div>
                      <div class="label">Link Maps</div>
                      <input name="link_maps" value="{_val(last_rev.link_maps)}"/>
                    </div>
                  </div>
                </div>
              </fieldset>

              <fieldset class="box revSection">
                <legend>Presupuesto / Pago</legend>
                <div class="revSectionBody">
                  <div class="grid">
                    <div>
                      <div class="label">Precio base</div>
                      <input name="precio_base" type="number" value="{last_rev.precio_base or ''}"/>
                    </div>
                    <div>
                      <div class="label">Viáticos</div>
                      <input name="viaticos" type="number" value="{last_rev.viaticos or ''}"/>
                    </div>
                  </div>

                  <div class="grid" style="margin-top:8px;">
                    <div>
                      <div class="label">Precio total</div>
                      <input name="precio_total" type="number" value="{last_rev.precio_total or ''}" data-precio-total="1"/>
                      <div class="muted small revHint">Se bloquea cuando "Recalcular automático" est- en SI.</div>
                    </div>
                    <div>
                      <div class="label">Recalcular automático</div>
                      <select name="recalcular_presupuesto" data-recalcular-presupuesto="1">
                        <option value="true" selected>SI</option>
                        <option value="false">NO</option>
                      </select>
                    </div>
                  </div>

                  <div class="grid" style="margin-top:8px;">
                    <div>
                      <div class="label">Pago</div>
                      <select name="pago">
                        <option value="">-</option>
                        <option value="true" {"selected" if last_rev.pago is True else ""}>SI</option>
                        <option value="false" {"selected" if last_rev.pago is False else ""}>NO</option>
                      </select>
                    </div>
                    <div>
                      <div class="label">Medio de pago</div>
                      <select name="medio_pago">
                        <option value="">-</option>
                        {''.join(opt(last_rev.medio_pago, m) for m in MEDIOS_PAGO)}
                      </select>
                    </div>
                  </div>
                </div>
              </fieldset>

              <fieldset class="box revSection">
                <legend>Turno</legend>
                <div class="revSectionBody">
                  <div class="grid">
                    <div>
                      <div class="label">Turno fecha</div>
                      <input type="date" name="turno_fecha" value="{tf_val}" data-turno-fecha="1"/>
                    </div>
                    <div>
                      <div class="label">Turno hora</div>
                      <select name="turno_hora" data-turno-hora="1">
                        <option value="">-</option>
                        {f'<option value="{th_val}" selected>{th_val}</option>' if th_val else ''}
                      </select>
                    </div>
                  </div>
                  <div class="muted small revHint" data-schedule-help="1">Seleccioná una fecha para cargar horarios válidos.</div>
                  <div class="muted small revHint" data-schedule-error="1" style="display:none; color:#b91c1c;"></div>
                  <div class="revHint" data-schedule-suggestions="1" style="display:none;"></div>

                  <div class="grid" style="margin-top:8px;">
                    <div>
                      <div class="label">Cliente presente</div>
                      <select name="cliente_presente">
                        <option value="">-</option>
                        <option value="true" {"selected" if last_rev.cliente_presente is True else ""}>SI</option>
                        <option value="false" {"selected" if last_rev.cliente_presente is False else ""}>NO</option>
                      </select>
                    </div>
                    <div>
                      <div class="label">Notas turno</div>
                      <textarea name="turno_notas">{_val(last_rev.turno_notas)}</textarea>
                    </div>
                  </div>
                  {_render_revision_approval_ui(last_rev, include_actions=True, lead_id=lead_id)}
                </div>
              </fieldset>

              <fieldset class="box revSection">
                <legend>Resultado</legend>
                <div class="revSectionBody">
                  <div class="grid">
                    <div>
                      <div class="label">Estado operativo</div>
                      <select name="estado_revision">
                        <option value="">-</option>
                        {''.join(opt(last_rev.estado_revision, s) for s in ESTADO_REVISION_OPCIONES)}
                      </select>
                    </div>
                    <div>
                      <div class="label">Resultado</div>
                      <input name="resultado" value="{_val(last_rev.resultado)}"/>
                    </div>
                  </div>
                  <div class="grid-1" style="margin-top:8px;">
                    <div>
                      <div class="label">Motivo rechazo</div>
                      <input name="motivo_rechazo" value="{_val(last_rev.motivo_rechazo)}"/>
                    </div>
                  </div>
                </div>
              </fieldset>

              {"<div class='grid-1' style='margin-top:8px;'><div class='label'>Informe PDF</div><div class='muted small'>Listo para activar cuando agreguemos Revision.informe_pdf en el modelo.</div></div>" if has_pdf else ""}
            </div>

            <div class="revModalFooter">
              <button class="btn btn-primary" type="submit">Guardar</button>
              <button class="btn" type="button" onclick="closeEditLatest({lead_id})">Cancelar</button>
            </div>
          </form>
        </div>
      </div>
      <script>
        (function () {{
          var root = document.getElementById("editrev-{lead_id}");
          if (!root) return;
          var form = root.querySelector('form[action="/ui/revision_latest_update"]');
          var sel = root.querySelector('select[data-tipo-vendedor="1"]');
          var wrap = root.querySelector('[data-agencia-wrap="1"]');
          var agenciaSelect = root.querySelector('select[data-agencia-select="1"]');
          var recalcSel = root.querySelector('select[data-recalcular-presupuesto="1"]');
          var totalInput = root.querySelector('input[data-precio-total="1"]');
          var turnoDateInput = root.querySelector('[data-turno-fecha="1"]');
          var turnoTimeSelect = root.querySelector('[data-turno-hora="1"]');
          var scheduleHelp = root.querySelector('[data-schedule-help="1"]');
          var scheduleError = root.querySelector('[data-schedule-error="1"]');
          var scheduleSuggestions = root.querySelector('[data-schedule-suggestions="1"]');
          var submitBtn = form ? form.querySelector('button[type="submit"]') : null;
          var scheduleRequestId = 0;
          function syncAgencia() {{
            if (!sel || !wrap) return;
            var show = (sel.value || "") === "AGENCIA";
            wrap.style.display = show ? "" : "none";
            if (agenciaSelect) agenciaSelect.disabled = !show;
          }}
          function syncPrecioTotalReadonly() {{
            if (!recalcSel || !totalInput) return;
            var autoMode = (recalcSel.value || "true") === "true";
            totalInput.readOnly = autoMode;
            totalInput.classList.toggle("revReadonly", autoMode);
          }}
          function trimValue(el) {{
            return ((el && el.value) || "").trim();
          }}
          function scheduleAddress() {{
            var direccionInput = form ? form.querySelector('input[name="direccion_texto"]') : null;
            var zoneGroupInput = form ? form.querySelector('select[name="zone_group"]') : null;
            var zoneDetailInput = form ? form.querySelector('select[name="zone_detail"]') : null;
            return trimValue(direccionInput) || trimValue(zoneDetailInput) || trimValue(zoneGroupInput) || "Sin dirección";
          }}
          function buildSchedulePayload(includeTime) {{
            var zoneGroupInput = form ? form.querySelector('select[name="zone_group"]') : null;
            var zoneDetailInput = form ? form.querySelector('select[name="zone_detail"]') : null;
            return {{
              address: scheduleAddress(),
              preferred_day: trimValue(turnoDateInput),
              preferred_time: includeTime ? trimValue(turnoTimeSelect) : "09:00",
              zone_group: trimValue(zoneGroupInput) || null,
              zone_detail: trimValue(zoneDetailInput) || null,
              exclude_revision_id: form ? Number(form.getAttribute("data-revision-id") || "0") || null : null,
            }};
          }}
          function clearScheduleFeedback() {{
            if (scheduleError) {{
              scheduleError.textContent = "";
              scheduleError.style.display = "none";
            }}
            if (scheduleSuggestions) {{
              scheduleSuggestions.innerHTML = "";
              scheduleSuggestions.style.display = "none";
            }}
          }}
          function slotToTime(slot) {{
            var text = String(slot || "");
            var parts = text.split("T");
            if (parts.length < 2) return "";
            return parts[1].slice(0, 5);
          }}
          function applySuggestedSlot(slot) {{
            if (!turnoDateInput || !turnoTimeSelect) return;
            var text = String(slot || "");
            var parts = text.split("T");
            if (parts.length < 2) return;
            turnoDateInput.value = parts[0];
            loadScheduleSlots(parts[1].slice(0, 5));
          }}
          function renderSuggestions(slots) {{
            if (!scheduleSuggestions) return;
            scheduleSuggestions.innerHTML = "";
            if (!slots || !slots.length) {{
              scheduleSuggestions.style.display = "none";
              return;
            }}
            var label = document.createElement("div");
            label.className = "muted small";
            label.textContent = "Alternativas sugeridas:";
            scheduleSuggestions.appendChild(label);
            var row = document.createElement("div");
            row.className = "revScheduleSuggestions";
            slots.forEach(function (slot) {{
              var btn = document.createElement("button");
              btn.type = "button";
              btn.className = "revScheduleChip";
              btn.textContent = String(slot).replace("T", " ");
              btn.addEventListener("click", function () {{
                clearScheduleFeedback();
                applySuggestedSlot(slot);
              }});
              row.appendChild(btn);
            }});
            scheduleSuggestions.appendChild(row);
            scheduleSuggestions.style.display = "";
          }}
          function showScheduleError(message, slots) {{
            if (scheduleError) {{
              scheduleError.textContent = message;
              scheduleError.style.display = "";
            }}
            renderSuggestions(slots || []);
          }}
          function populateTimeOptions(slots, preferredTime, invalidMessage) {{
            if (!turnoTimeSelect) return;
            var desired = (preferredTime || trimValue(turnoTimeSelect) || "").slice(0, 5);
            turnoTimeSelect.innerHTML = '<option value="">-</option>';
            var seen = {{}};
            (slots || []).forEach(function (slot) {{
              var timeValue = slotToTime(slot);
              if (!timeValue || seen[timeValue]) return;
              seen[timeValue] = true;
              var option = document.createElement("option");
              option.value = timeValue;
              option.textContent = timeValue;
              if (timeValue === desired) option.selected = true;
              turnoTimeSelect.appendChild(option);
            }});
            if (desired && !seen[desired]) {{
              var fallback = document.createElement("option");
              fallback.value = desired;
              fallback.textContent = invalidMessage || (desired + " (ya no disponible)");
              fallback.selected = true;
              turnoTimeSelect.appendChild(fallback);
            }}
          }}
          function updateScheduleHelp(text) {{
            if (scheduleHelp) scheduleHelp.textContent = text;
          }}
          function loadScheduleSlots(preferredTime) {{
            clearScheduleFeedback();
            if (!form || !turnoDateInput || !turnoTimeSelect) return Promise.resolve();
            var day = trimValue(turnoDateInput);
            if (!day) {{
              populateTimeOptions([], "", "");
              updateScheduleHelp("Seleccioná una fecha para cargar horarios válidos.");
              return Promise.resolve();
            }}
            var params = new URLSearchParams();
            var payload = buildSchedulePayload(false);
            params.set("preferred_day", day);
            params.set("address", payload.address);
            if (payload.zone_group) params.set("zone_group", payload.zone_group);
            if (payload.zone_detail) params.set("zone_detail", payload.zone_detail);
            if (payload.exclude_revision_id) params.set("exclude_revision_id", String(payload.exclude_revision_id));
            var requestId = ++scheduleRequestId;
            updateScheduleHelp("Cargando horarios válidos...");
            return fetch("/api/schedule/slots?" + params.toString(), {{
              headers: {{ "Accept": "application/json" }}
            }}).then(function (res) {{
              if (!res.ok) throw new Error("schedule_slots_failed");
              return res.json();
            }}).then(function (data) {{
              if (requestId !== scheduleRequestId) return;
              var desired = (preferredTime || trimValue(turnoTimeSelect) || "").slice(0, 5);
              var validTimes = (data.slots || []).map(slotToTime).filter(Boolean);
              populateTimeOptions(data.slots || [], desired, desired ? (desired + " (inválido)") : "");
              updateScheduleHelp("Horarios disponibles: " + (data.business_hours || "-"));
              if (desired && validTimes.indexOf(desired) === -1) {{
                showScheduleError("El horario seleccionado ya no es válido para esta agenda.", data.slots || []);
              }}
            }}).catch(function () {{
              if (requestId !== scheduleRequestId) return;
              updateScheduleHelp("No se pudieron cargar horarios válidos. Podés volver a intentar.");
            }});
          }}
          function validateSchedule() {{
            if (!turnoDateInput || !turnoTimeSelect) return Promise.resolve(true);
            var day = trimValue(turnoDateInput);
            var timeValue = trimValue(turnoTimeSelect);
            if (!day || !timeValue) return Promise.resolve(true);
            clearScheduleFeedback();
            var payload = buildSchedulePayload(true);
            return fetch("/api/schedule/check", {{
              method: "POST",
              headers: {{
                "Content-Type": "application/json",
                "Accept": "application/json"
              }},
              body: JSON.stringify(payload)
            }}).then(function (res) {{
              if (!res.ok) throw new Error("schedule_check_failed");
              return res.json();
            }}).then(function (data) {{
              if (!data.valid) {{
                var firstReason = (data.reasons && data.reasons[0]) || "El turno seleccionado no es válido.";
                showScheduleError(firstReason, data.suggested_slots || []);
                return false;
              }}
              updateScheduleHelp((data.approval_tag || "Turno valido") + " - " + (data.business_hours || "-"));
              return true;
            }}).catch(function () {{
              showScheduleError("No se pudo validar el turno. Probá de nuevo.", []);
              return false;
            }});
          }}
          if (sel) sel.addEventListener("change", syncAgencia);
          if (recalcSel) recalcSel.addEventListener("change", syncPrecioTotalReadonly);
          if (turnoDateInput) turnoDateInput.addEventListener("change", function () {{
            loadScheduleSlots("");
          }});
          ['input[name="direccion_texto"]', 'select[name="zone_group"]', 'select[name="zone_detail"]'].forEach(function (selector) {{
            var field = form ? form.querySelector(selector) : null;
            if (!field) return;
            field.addEventListener("change", function () {{
              if (trimValue(turnoDateInput)) {{
                window.setTimeout(function () {{
                  loadScheduleSlots(trimValue(turnoTimeSelect));
                }}, 0);
              }}
            }});
          }});
          if (form) {{
            form.addEventListener("submit", function (e) {{
              if (form.getAttribute("data-schedule-validated") === "1") return;
              var hasDate = trimValue(turnoDateInput);
              var hasTime = trimValue(turnoTimeSelect);
              if (!hasDate || !hasTime) return;
              e.preventDefault();
              if (submitBtn) submitBtn.disabled = true;
              validateSchedule().then(function (valid) {{
                if (!valid) return;
                form.setAttribute("data-schedule-validated", "1");
                form.submit();
              }}).finally(function () {{
                if (submitBtn) submitBtn.disabled = false;
              }});
            }});
          }}
          syncAgencia();
          syncPrecioTotalReadonly();
          loadScheduleSlots(form ? form.getAttribute("data-current-turno-time") || "" : "");
        }})();
      </script>
    """


def render_revisions_table_page(
    revisions: list[Revision],
    profesionales: list[Profesional] | None = None,
    user_email: str = "",
    q: str = "",
    estado: list[str] | None = None,
    flag: list[str] | None = None,
    profesional_id: str = "",
    canal: str = "",
    tipo_vehiculo: str = "",
    marca: str = "",
    modelo: str = "",
    anio: str = "",
    zone_group: str = "",
    zone_detail: str = "",
    estado_revision: str = "",
    from_date: str = "",
    to_date: str = "",
    date_field: str = "turno",
    zones_map: dict[str, list[str]] | None = None,
    open_filters: bool = False,
) -> str:
    table_css = """
      .tableWrap { overflow:auto; background: rgba(255,255,255,.75); border:1px solid var(--border); border-radius:14px; box-shadow:var(--shadow); max-height:calc(100vh - 220px); }
      table { width:100%; border-collapse:collapse; min-width:1450px; }
      th, td { padding:8px 10px; border-bottom:1px solid var(--border); text-align:left; vertical-align:top; }
      thead th { font-size:12px; color:#374151; background:#fff; position:sticky; top:0; z-index:5; box-shadow:0 1px 0 rgba(0,0,0,.08); }
      td { font-size:13px; }
      tr:hover td { background:#f3f4f6; }
      .tableHeader { display:flex; align-items:center; justify-content:space-between; gap:10px; margin-bottom:12px; }
      .tableSubtitle { font-size:13px; font-weight:700; color:#111827; background:rgba(255,255,255,.9); border:1px solid var(--border); border-radius:999px; padding:4px 10px; }
      .tableTopActions { display:flex; gap:8px; align-items:center; }
      .iconActionBtn { border:1px solid var(--border); background:#fff; border-radius:10px; padding:6px 8px; cursor:pointer; display:inline-flex; align-items:center; }
      .iconActionBtn:hover { background:#f9fafb; }
      .chips { display:flex; flex-wrap:wrap; gap:8px; margin:8px 0 12px; }
      .chip { display:inline-flex; align-items:center; gap:8px; padding:6px 10px; border-radius:999px; border:1px solid var(--border); background:#fff; font-size:12px; text-decoration:none; color:#111827; }
      .chip .x { opacity:.6; }
      @media (max-width: 768px) {
        .kanbanTopBar { display: flex !important; top: 52px; }
        .kanbanTopBarTitle, .buildStamp { display: none !important; }
      }
    """
    css = _base_css(extra_css=table_css)
    build_stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    search_val = html_lib.escape(_val(q), quote=True)

    params = {
        "q": q,
        "estado": estado or [],
        "flag": flag or [],
        "profesional_id": profesional_id,
        "canal": canal,
        "tipo_vehiculo": tipo_vehiculo,
        "marca": marca,
        "modelo": modelo,
        "anio": anio,
        "zone_group": zone_group,
        "zone_detail": zone_detail,
        "estado_revision": estado_revision,
        "from_date": from_date,
        "to_date": to_date,
        "date_field": date_field,
    }
    query = _build_query_string(params)
    kanban_href = f"/kanban?{query}" if query else "/kanban"

    def _canal_label(val: str) -> str:
        mapping = {
            "IG_DM": "Instagram DM",
            "IG_WHATSAPP": "Instagram WhatsApp",
            "FB_DM": "Facebook DM",
            "FB_WHATSAPP": "Facebook WhatsApp",
            "WEBSITE": "Website",
            "GOOGLE": "Google",
            "GMAPS": "Google Maps",
            "OTROS": "Otros",
        }
        return mapping.get(val, val.replace("_", " ").title())

    def _make_table_link(new_params: dict[str, Any]) -> str:
        qstr = _build_query_string(new_params)
        return f"/table?{qstr}" if qstr else "/table"

    chips: list[str] = []
    active_params = dict(params)
    estado_list = list(active_params.get("estado") or [])
    if estado_list:
        for st in estado_list:
            p = dict(active_params)
            p["estado"] = [x for x in estado_list if x != st]
            chips.append(f'<a class="chip" href="{_make_table_link(p)}">Estado: {KANBAN_LABELS.get(st, st)}<span class="x">-</span></a>')
    flag_list = list(active_params.get("flag") or [])
    if flag_list:
        for fv in flag_list:
            p = dict(active_params)
            p["flag"] = [x for x in flag_list if x != fv]
            chips.append(f'<a class="chip" href="{_make_table_link(p)}">Flag: {FLAG_LABELS.get(fv, fv)}<span class="x">-</span></a>')
    if _val(profesional_id):
        p = dict(active_params)
        p["profesional_id"] = ""
        label = "-"
        try:
            pid = int(_val(profesional_id))
        except ValueError:
            pid = None
        if pid:
            prof_lookup = {pr.id: pr for pr in (profesionales or [])}
            prof = prof_lookup.get(pid)
            if prof:
                label = _profesional_label(prof)
        chips.append(f'<a class="chip" href="{_make_table_link(p)}">Profesional: {label}<span class="x">-</span></a>')
    if _val(canal):
        p = dict(active_params)
        p["canal"] = ""
        chips.append(f'<a class="chip" href="{_make_table_link(p)}">Canal: {_canal_label(_val(canal))}<span class="x">-</span></a>')
    if _val(marca):
        p = dict(active_params)
        p["marca"] = ""
        chips.append(f'<a class="chip" href="{_make_table_link(p)}">Marca: {_txt(marca)}<span class="x">-</span></a>')
    if _val(modelo):
        p = dict(active_params)
        p["modelo"] = ""
        chips.append(f'<a class="chip" href="{_make_table_link(p)}">Modelo: {_txt(modelo)}<span class="x">-</span></a>')
    if _val(tipo_vehiculo):
        p = dict(active_params)
        p["tipo_vehiculo"] = ""
        chips.append(f'<a class="chip" href="{_make_table_link(p)}">Tipo vehículo: {_txt(tipo_vehiculo)}<span class="x">-</span></a>')
    if _val(anio):
        p = dict(active_params)
        p["anio"] = ""
        chips.append(f'<a class="chip" href="{_make_table_link(p)}">Año: {_txt(anio)}<span class="x">-</span></a>')
    if _val(zone_group):
        p = dict(active_params)
        p["zone_group"] = ""
        chips.append(f'<a class="chip" href="{_make_table_link(p)}">Zona grupo: {_txt(zone_group)}<span class="x">-</span></a>')
    if _val(zone_detail):
        p = dict(active_params)
        p["zone_detail"] = ""
        chips.append(f'<a class="chip" href="{_make_table_link(p)}">Zona detalle: {_txt(zone_detail)}<span class="x">-</span></a>')
    if _val(estado_revision):
        p = dict(active_params)
        p["estado_revision"] = ""
        chips.append(f'<a class="chip" href="{_make_table_link(p)}">Estado revisión: {_txt(estado_revision)}<span class="x">-</span></a>')
    if _val(from_date) or _val(to_date):
        p = dict(active_params)
        p["from_date"] = ""
        p["to_date"] = ""
        field_label = "Turno" if _val(date_field) != "created" else "Creada"
        if _val(from_date) and _val(to_date):
            label = f"{field_label}: {from_date} ? {to_date}"
        elif _val(from_date):
            label = f"{field_label} desde: {from_date}"
        else:
            label = f"{field_label} hasta: {to_date}"
        chips.append(f'<a class="chip" href="{_make_table_link(p)}">{label}<span class="x">-</span></a>')
    if _val(q):
        p = dict(active_params)
        p["q"] = ""
        chips.append(f'<a class="chip" href="{_make_table_link(p)}">Buscar: {_txt(q)}<span class="x">-</span></a>')
    if chips:
        chips.append('<a class="chip" href="/table">Limpiar todo<span class="x">-</span></a>')

    filters_form_html = _filters_form_html(
        q=q,
        estado=estado,
        flag=flag,
        profesional_id=profesional_id,
        profesionales=profesionales or [],
        canal=canal,
        tipo_vehiculo=tipo_vehiculo,
        marca=marca,
        modelo=modelo,
        anio=anio,
        zone_group=zone_group,
        zone_detail=zone_detail,
        estado_revision=estado_revision,
        from_date=from_date,
        to_date=to_date,
        date_field=date_field,
        zones_map=zones_map,
        action="/table",
        include_back_link=True,
        back_href=kanban_href,
        include_open_filters=True,
    )

    total_precio = sum((r.precio_total or 0) for r in revisions if r.precio_total is not None)
    icon_board = '<svg class="icon" viewBox="0 0 24 24"><rect x="3" y="4" width="7" height="7"/><rect x="14" y="4" width="7" height="7"/><rect x="3" y="15" width="7" height="7"/><rect x="14" y="15" width="7" height="7"/></svg>'
    icon_calendar = '<svg class="icon" viewBox="0 0 24 24"><rect x="3" y="5" width="18" height="16" rx="2"/><path d="M8 3v4M16 3v4M3 11h18"/></svg>'
    icon_filter = '<svg class="icon" viewBox="0 0 24 24"><path d="M4 6h16l-6 7v5l-4 2v-7z"/></svg>'
    icon_prof = '<svg class="icon" viewBox="0 0 24 24"><circle cx="12" cy="7" r="4"/><path d="M4 21c1.5-4 14.5-4 16 0"/></svg>'
    icon_ag = '<svg class="icon" viewBox="0 0 24 24"><path d="M3 21h18"/><path d="M5 21V8l7-5 7 5v13"/><path d="M9 12h6"/></svg>'
    icon_toggle = '<svg class="icon icon-only" viewBox="0 0 24 24"><path d="M15 6l-6 6 6 6"/></svg>'

    rows: list[str] = []
    for r in revisions:
        l = r.lead
        if not l:
            continue
        flag_val = _lead_flag_value(l)
        flag_label = FLAG_LABELS.get(flag_val, flag_val) if flag_val else "-"
        turno_txt = "-"
        if r.turno_fecha or r.turno_hora:
            tf = r.turno_fecha.isoformat() if r.turno_fecha else "-"
            th = r.turno_hora.strftime("%H:%M") if r.turno_hora else "-"
            turno_txt = f"{tf} {th}"
        prof_name = ""
        if r.profesional:
            prof_name = _profesional_label(r.profesional)
        agencia_name = ""
        if r.agencia:
            agencia_name = _val(r.agencia.nombre_agencia)
        search_text = html_lib.escape(
            " ".join([
                _val(l.nombre),
                _val(l.apellido),
                _val(l.telefono),
                _val(l.email),
                _val(r.marca),
                _val(r.modelo),
                _val(r.anio),
                _val(prof_name),
                _val(r.estado_revision),
                _val(agencia_name),
            ]),
            quote=True,
        )
        raw_total = float(r.precio_total) if r.precio_total is not None else 0.0
        rows.append(f"""
          <tr data-search="{search_text}" data-total="{raw_total:.2f}">
            <td>{r.id}</td>
            <td>{r.created_at.strftime("%Y-%m-%d %H:%M") if r.created_at else "-"}</td>
            <td>{l.id}</td>
            <td>{_txt(l.nombre)} {_txt(l.apellido)}</td>
            <td>{_txt(l.telefono)}</td>
            <td>{_txt(l.email)}</td>
            <td>{_txt(_lead_operational_estado(_get(l, "estado")))}</td>
            <td>{_txt(flag_label)}</td>
            <td>{_txt(r.tipo_vehiculo)}</td>
            <td>{_txt(r.marca)}</td>
            <td>{_txt(r.modelo)}</td>
            <td>{_txt(r.anio)}</td>
            <td>{_txt(r.zone_group)}</td>
            <td>{_txt(r.zone_detail)}</td>
            <td>{turno_txt}</td>
            <td>{_txt(r.estado_revision)}</td>
            <td>{_fmt_money(r.precio_total)}</td>
            <td><a class="btn btn-sm" href="{kanban_href}#lead-{l.id}">Abrir</a></td>
          </tr>
        """)

    html: list[str] = [css]
    html.append('<div class="layout">')
    html.append("""
      <aside class="sidebar" id="sidebar">
        <div class="brandRow">
          <div class="brandText">RIDECHECK</div>
          <button class="sidebarToggle" type="button" onclick="toggleSidebar()" title="Collapse sidebar">%s</button>
        </div>
        %s
        %s
      </aside>
    """ % (
        icon_toggle,
        render_sidebar_nav(
            icon_board=icon_board,
            icon_calendar=icon_calendar,
            icon_filter=icon_filter,
            icon_prof=icon_prof,
            icon_ag=icon_ag,
            icon_wa=ICON_WHATSAPP,
        ),
        _sidebar_user_block(user_email),
    ))
    html.append('<main class="main">')
    html.append(f"""
      <div class="kanbanTopBar">
        <div class="kanbanTopBarTitle">Revisiones</div>
        <div class="kanbanTopBarRight">
          <span class="buildStamp">build: {build_stamp}</span>
          <div class="searchControl" id="table-search-control">
            <button class="iconBtn" id="table-search-toggle" type="button" title="Buscar (Ctrl+F)" aria-expanded="false">{ICON_SEARCH}</button>
            <div class="searchBoxWrap" id="table-search-wrap">
              <input id="table-search-input" class="searchInput" type="text" placeholder="Buscar en resultados..." value="{search_val}"/>
              <span id="table-search-count" class="searchCount">0 / 0</span>
              <button class="iconBtn" id="table-search-close" type="button" title="Cerrar búsqueda">{ICON_CLOSE}</button>
            </div>
          </div>
          <button class="iconActionBtn" type="button" onclick="openFilters()" title="Filtros" aria-label="Filtros">{ICON_MENU_HAMBURGER}</button>
        </div>
      </div>
      <div class="tableHeader">
        <div class="tableSubtitle">Revisiones: <span id="rev-visible-count">{len(revisions)}</span> | Total: <span id="rev-visible-total">{_fmt_money(total_precio)}</span></div>
      </div>
    """)
    html.append("""
      <div id="drawerOverlay" class="drawerOverlay%s" onclick="closeFilters()"></div>
      <div id="filtersDrawer" class="drawer%s" role="dialog" aria-label="Filtros">
        <div class="menuTitle">Filtros</div>
        %s
      </div>
    """ % (" open" if open_filters else "", " open" if open_filters else "", filters_form_html))
    if chips:
        html.append('<div class="chips">%s</div>' % "".join(chips))
    html.append("""
      <div class="tableWrap" data-search-scope="table">
        <table>
          <thead>
            <tr>
              <th>Revisión ID</th><th>Creada</th><th>Lead ID</th><th>Cliente</th><th>Tel</th><th>Email</th><th>Lead estado</th><th>Flag</th>
              <th>Tipo vehículo</th><th>Marca</th><th>Modelo</th><th>Año</th><th>Zona grupo</th><th>Zona detalle</th><th>Turno</th><th>Estado revisión</th><th>Precio total</th><th></th>
            </tr>
          </thead>
          <tbody>%s</tbody>
        </table>
      </div>
    """ % "\n".join(rows))
    zones_json = json.dumps(zones_map or {}, ensure_ascii=False).replace("</", "<\\/")
    html.append(f'<script type="application/json" id="zones-data">{zones_json}</script>')
    html.append("""
      <script>
        (function () {
          var zonesEl = document.getElementById("zones-data");
          var zonesMap = {};
          if (zonesEl && zonesEl.textContent) { try { zonesMap = JSON.parse(zonesEl.textContent); } catch(e) {} }
          var searchControl = document.getElementById("table-search-control");
          var searchToggleBtn = document.getElementById("table-search-toggle");
          var searchInput = document.getElementById("table-search-input");
          var searchCloseBtn = document.getElementById("table-search-close");
          var searchCount = document.getElementById("table-search-count");
          var searchScope = document.querySelector('[data-search-scope="table"]');
          var visibleCountEl = document.getElementById("rev-visible-count");
          var visibleTotalEl = document.getElementById("rev-visible-total");
          function n(v){ return (v||"").toString().normalize("NFD").replace(/[\\u0300-\\u036f]/g,"").toLowerCase().trim(); }
          function fmtMoney(v) {
            try {
              return Number(v || 0).toLocaleString("es-AR", { style: "currency", currency: "ARS", maximumFractionDigits: 0 });
            } catch (e) {
              return "$ " + Math.round(Number(v || 0));
            }
          }
          function updateVisibleSummary(visibleRows) {
            var total = 0;
            visibleRows.forEach(function (row) {
              var raw = parseFloat(row.getAttribute("data-total") || "0");
              if (!Number.isNaN(raw)) total += raw;
            });
            if (visibleCountEl) visibleCountEl.textContent = String(visibleRows.length);
            if (visibleTotalEl) visibleTotalEl.textContent = fmtMoney(total);
          }
          function applyTableSearch() {
            if (!searchScope) return;
            var q = n(searchInput ? searchInput.value : "");
            var rows = searchScope.querySelectorAll("tbody tr");
            var total = 0, visible = 0;
            var visibleRows = [];
            rows.forEach(function (row) {
              total += 1;
              var haystack = n(row.getAttribute("data-search") || row.textContent);
              var show = !q || haystack.indexOf(q) !== -1;
              row.style.display = show ? "" : "none";
              if (show) {
                visible += 1;
                visibleRows.push(row);
              }
            });
            if (searchCount) searchCount.textContent = q ? (visible + " / " + total) : (total + " / " + total);
            updateVisibleSummary(visibleRows);
          }
          function openTableSearch(focusInput) {
            if (!searchControl || !searchToggleBtn) return;
            searchControl.classList.add("open");
            searchToggleBtn.setAttribute("aria-expanded", "true");
            if (focusInput && searchInput) {
              searchInput.focus();
              searchInput.select();
            }
            applyTableSearch();
          }
          function closeTableSearch(clearValue) {
            if (!searchControl || !searchToggleBtn) return;
            if (clearValue && searchInput) searchInput.value = "";
            searchControl.classList.remove("open");
            searchToggleBtn.setAttribute("aria-expanded", "false");
            applyTableSearch();
          }
          if (searchToggleBtn) {
            searchToggleBtn.addEventListener("click", function () {
              var isOpen = searchControl && searchControl.classList.contains("open");
              if (isOpen) {
                closeTableSearch(false);
                return;
              }
              openTableSearch(true);
            });
          }
          function refreshZoneDetails(scope) {
            var groupSel = scope.querySelector('select[data-zone-group]');
            var detailSel = scope.querySelector('select[data-zone-detail]');
            if (!groupSel || !detailSel) return;
            var opts = zonesMap[groupSel.value || ""] || [];
            var cur = detailSel.value || "";
            detailSel.innerHTML = '<option value="">-</option>';
            opts.forEach(function (d) {
              var o = document.createElement("option");
              o.value = d; o.textContent = d; if (d === cur) o.selected = true;
              detailSel.appendChild(o);
            });
          }
          document.addEventListener("change", function (e) {
            if (e.target && e.target.matches('select[data-zone-group]')) {
              refreshZoneDetails(e.target.closest("form") || document);
            }
          });
          function setSidebarCollapsed(collapsed) {
            var sb = document.getElementById("sidebar");
            if (!sb) return;
            sb.classList.toggle("collapsed", collapsed);
            localStorage.setItem("sidebar_collapsed", collapsed ? "1" : "0");
          }
          window.toggleSidebar = function () {
            var sb = document.getElementById("sidebar");
            if (!sb) return;
            setSidebarCollapsed(!sb.classList.contains("collapsed"));
          };
          window.openFilters = function () {
            var drawer = document.getElementById("filtersDrawer");
            var overlay = document.getElementById("drawerOverlay");
            if (!drawer || !overlay) return;
            drawer.classList.add("open");
            overlay.classList.add("open");
          };
          window.closeFilters = function () {
            var drawer = document.getElementById("filtersDrawer");
            var overlay = document.getElementById("drawerOverlay");
            if (!drawer || !overlay) return;
            drawer.classList.remove("open");
            overlay.classList.remove("open");
          };
          if (searchCloseBtn) searchCloseBtn.addEventListener("click", function(){ if (searchInput) searchInput.value = ""; applyTableSearch(); });
          if (searchInput) searchInput.addEventListener("input", applyTableSearch);
          document.addEventListener("keydown", function (e) {
            if ((e.key || "") === "Escape") {
              closeFilters();
            }
            if (!(e.ctrlKey || e.metaKey)) return;
            if ((e.key || "").toLowerCase() !== "f") return;
            e.preventDefault();
            openTableSearch(true);
          }, true);
          window.addEventListener("DOMContentLoaded", function () {
            setSidebarCollapsed(localStorage.getItem("sidebar_collapsed") === "1");
            refreshZoneDetails(document);
            if (searchInput && (searchInput.value || "").trim()) {
              openTableSearch(false);
            }
            applyTableSearch();
          });
        })();
      </script>
    """)
    html.append("</main></div>")
    return "\n".join(html)


def render_agencias_page(
    agencias: list[Agencia],
    vendedores: list[Vendedor],
    user_email: str = "",
) -> str:
    table_css = """
      .tableWrap { overflow:auto; background: rgba(255,255,255,.72); border: 1px solid var(--border); border-radius: 14px; box-shadow: var(--shadow); }
      table { width:100%; border-collapse:collapse; min-width:1200px; }
      th, td { padding:8px 10px; border-bottom:1px solid var(--border); text-align:left; vertical-align:top; }
      thead th { font-size:12px; color:#374151; background:#fff; position:sticky; top:0; z-index:5; }
      .agModalOverlay { position:fixed; inset:0; background:rgba(17,24,39,.45); display:none; align-items:center; justify-content:center; padding:12px; z-index:1300; }
      .agModalOverlay.open { display:flex; }
      .agModal { width:min(820px, 96vw); max-height:calc(100vh - 24px); background:#fff; border:1px solid var(--border); border-radius:14px; box-shadow: var(--shadow2); overflow:hidden; display:flex; flex-direction:column; }
      .agModalHead { display:flex; justify-content:space-between; align-items:center; gap:8px; padding:10px 12px; border-bottom:1px solid var(--border); }
      .agModalBody { padding:12px; overflow:auto; }
      .agModalFoot { padding:10px 12px; border-top:1px solid var(--border); display:flex; gap:8px; justify-content:flex-end; flex-wrap:wrap; }
      @media (max-width: 740px) { .agModal { width:100vw; height:100vh; max-height:none; border-radius:0; } }
      @media (max-width: 768px) { .grid { grid-template-columns: 1fr; } .grid input, .grid select { width: 100%; box-sizing: border-box; } .main { padding: 60px 12px 0 !important; } }
    """
    css = _base_css(extra_css=table_css)
    build_stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    vend_opts = "".join([f'<option value="{v.id}">{_txt(v.nombre)}</option>' for v in vendedores])
    icon_board = '<svg class="icon" viewBox="0 0 24 24"><rect x="3" y="4" width="7" height="7"/><rect x="14" y="4" width="7" height="7"/><rect x="3" y="15" width="7" height="7"/><rect x="14" y="15" width="7" height="7"/></svg>'
    icon_calendar = '<svg class="icon" viewBox="0 0 24 24"><rect x="3" y="5" width="18" height="16" rx="2"/><path d="M8 3v4M16 3v4M3 11h18"/></svg>'
    icon_filter = '<svg class="icon" viewBox="0 0 24 24"><path d="M4 6h16l-6 7v5l-4 2v-7z"/></svg>'
    icon_prof = '<svg class="icon" viewBox="0 0 24 24"><circle cx="12" cy="7" r="4"/><path d="M4 21c1.5-4 14.5-4 16 0"/></svg>'
    icon_ag = '<svg class="icon" viewBox="0 0 24 24"><path d="M3 21h18"/><path d="M5 21V8l7-5 7 5v13"/><path d="M9 12h6"/></svg>'
    icon_toggle = '<svg class="icon icon-only" viewBox="0 0 24 24"><path d="M15 6l-6 6 6 6"/></svg>'

    rows: list[str] = []
    modals: list[str] = []
    for a in agencias:
        vend_name = _txt(a.vendedor.nombre if a.vendedor else None)
        file_name = _txt(a.file_name)
        file_cell = f'<a href="/ui/agencia_file/{a.id}">{file_name}</a>' if a.file_path else "-"
        search_text = html_lib.escape(
            " ".join([
                _val(a.nombre_agencia),
                _val(a.direccion),
                _val(a.mail),
                _val(vend_name),
                _val(a.telefono),
                _val(a.file_name),
            ]),
            quote=True,
        )
        rows.append(f"""
          <tr data-search="{search_text}">
            <td>{a.id}</td><td>{_txt(a.nombre_agencia)}</td><td>{_txt(a.direccion)}</td><td>{_url_link(a.gmaps, "Maps")}</td><td>{_txt(a.mail)}</td>
            <td>{vend_name}</td><td>{_txt(a.telefono)}</td><td>{file_cell}</td><td>{a.fecha_subido.strftime("%Y-%m-%d %H:%M") if a.fecha_subido else "-"}</td>
            <td><button class="btn btn-sm" type="button" onclick="openAgenciaEdit({a.id})">Editar</button></td>
          </tr>
        """)

        modals.append(f"""
          <div class="agModalOverlay" id="ag-modal-{a.id}" onclick="closeAgenciaEdit({a.id}, event)">
            <div class="agModal" role="dialog" aria-modal="true" aria-label="Editar agencia" onclick="event.stopPropagation();">
              <div class="agModalHead">
                <div class="menuTitle" style="margin:0;">Editar agencia #{a.id}</div>
                <button class="iconBtn" type="button" onclick="closeAgenciaEdit({a.id})" aria-label="Cerrar">{ICON_CLOSE}</button>
              </div>
              <form method="post" action="/ui/agencia_update" enctype="multipart/form-data">
                <input type="hidden" name="agencia_id" value="{a.id}"/>
                <div class="agModalBody">
                  <div class="grid">
                    <div><div class="label">Nombre agencia</div><input name="nombre_agencia" value="{_val(a.nombre_agencia)}" required/></div>
                    <div><div class="label">Dirección</div><input name="direccion" value="{_val(a.direccion)}"/></div>
                  </div>
                  <div class="grid" style="margin-top:8px;">
                    <div><div class="label">GMaps</div><input name="gmaps" value="{_val(a.gmaps)}"/></div>
                    <div><div class="label">Mail</div><input name="mail" value="{_val(a.mail)}"/></div>
                  </div>
                  <div class="grid" style="margin-top:8px;">
                    <div><div class="label">Vendedor</div><select name="vendedor_id"><option value="">-</option>{''.join([f'<option value="{v.id}" {"selected" if a.vendedor_id==v.id else ""}>{_txt(v.nombre)}</option>' for v in vendedores])}</select></div>
                    <div><div class="label">+ Nuevo vendedor (opcional)</div><input name="vendedor_nuevo"/></div>
                  </div>
                  <div class="grid" style="margin-top:8px;">
                    <div><div class="label">Teléfono</div><input name="telefono" value="{_val(a.telefono)}"/></div>
                    <div><div class="label">Archivo XLS</div><input name="file" type="file" accept=".xls,.xlsx"/></div>
                  </div>
                </div>
                <div class="agModalFoot">
                  <button class="btn btn-primary" type="submit">Guardar</button>
                  <button class="btn" type="button" onclick="closeAgenciaEdit({a.id})">Cancelar</button>
                </div>
              </form>
              <div class="agModalFoot" style="border-top:none; padding-top:0;">
                <form method="post" action="/ui/agencia_delete">
                  <input type="hidden" name="agencia_id" value="{a.id}"/>
                  <button class="btn btn-danger" type="submit">Eliminar</button>
                </form>
              </div>
            </div>
          </div>
        """)

    html = [css, '<div class="layout">']
    html.append("""
      <aside class="sidebar" id="sidebar">
        <div class="brandRow"><div class="brandText">RIDECHECK</div><button class="sidebarToggle" type="button" onclick="toggleSidebar()" title="Collapse sidebar">%s</button></div>
        %s
        %s
      </aside>
    """ % (
        icon_toggle,
        render_sidebar_nav(
            icon_board=icon_board,
            icon_calendar=icon_calendar,
            icon_filter=icon_filter,
            icon_prof=icon_prof,
            icon_ag=icon_ag,
            icon_wa=ICON_WHATSAPP,
        ),
        _sidebar_user_block(user_email),
    ))
    html.append('<main class="main">')
    html.append(f"""
      <div class="kanbanTopBar">
        <div class="kanbanTopBarTitle">Agencias</div>
        <div class="kanbanTopBarRight">
          <span class="buildStamp">build: {build_stamp}</span>
          <div class="searchControl" id="ag-search-control">
            <button class="iconBtn" id="ag-search-toggle" type="button" title="Buscar (Ctrl+F)" aria-expanded="false">{ICON_SEARCH}</button>
            <div class="searchBoxWrap" id="ag-search-wrap">
              <input id="ag-search-input" class="searchInput" type="text" placeholder="Buscar agencias..." value=""/>
              <span id="ag-search-count" class="searchCount">0 / 0</span>
              <button class="iconBtn" id="ag-search-close" type="button" title="Cerrar búsqueda">{ICON_CLOSE}</button>
            </div>
          </div>
        </div>
      </div>
      <div class="box" style="max-width:780px;">
        <div class="menuTitle">Agregar agencia</div>
        <form method="post" action="/ui/agencia_create" enctype="multipart/form-data" style="margin-top:8px;">
          <div class="grid"><div><div class="label">Nombre agencia</div><input name="nombre_agencia" required/></div><div><div class="label">Dirección</div><input name="direccion"/></div></div>
          <div class="grid" style="margin-top:8px;"><div><div class="label">GMaps</div><input name="gmaps"/></div><div><div class="label">Mail</div><input name="mail" type="email"/></div></div>
          <div class="grid" style="margin-top:8px;"><div><div class="label">Vendedor</div><select name="vendedor_id"><option value="">-</option>{vend_opts}</select></div><div><div class="label">+ Nuevo vendedor (opcional)</div><input name="vendedor_nuevo"/></div></div>
          <div class="grid" style="margin-top:8px;"><div><div class="label">Teléfono</div><input name="telefono"/></div><div><div class="label">Archivo XLS</div><input name="file" type="file" accept=".xls,.xlsx"/></div></div>
          <div class="stack" style="margin-top:10px;"><button class="btn btn-primary" type="submit">Crear</button></div>
        </form>
      </div>
      <div class="tableWrap" style="margin-top:12px;" data-search-scope="ag">
        <table>
          <thead><tr><th>ID</th><th>Agencia</th><th>Dirección</th><th>GMaps</th><th>Mail</th><th>Vendedor</th><th>Teléfono</th><th>Archivo</th><th>Fecha subido</th><th>Acciones</th></tr></thead>
          <tbody>{''.join(rows)}</tbody>
        </table>
      </div>
      {''.join(modals)}
    """)
    html.append("""
      <script>
        (function () {
          var searchControl = document.getElementById("ag-search-control");
          var searchInput = document.getElementById("ag-search-input");
          var searchToggleBtn = document.getElementById("ag-search-toggle");
          var searchCloseBtn = document.getElementById("ag-search-close");
          var searchCount = document.getElementById("ag-search-count");
          var searchScope = document.querySelector('[data-search-scope="ag"]');
          function normalizeSearchText(value) {
            return (value || "").toString().normalize("NFD").replace(/[\\u0300-\\u036f]/g, "").toLowerCase().trim();
          }
          function applyAgSearch() {
            if (!searchScope) return;
            var q = normalizeSearchText(searchInput ? searchInput.value : "");
            var rows = searchScope.querySelectorAll("tbody tr");
            var total = 0, visible = 0;
            rows.forEach(function (row) {
              total += 1;
              var haystack = normalizeSearchText(row.getAttribute("data-search") || row.textContent || "");
              var show = !q || haystack.indexOf(q) !== -1;
              row.style.display = show ? "" : "none";
              if (show) visible += 1;
            });
            if (searchCount) searchCount.textContent = q ? (visible + " / " + total) : (total + " / " + total);
          }
          function openAgSearch(focusInput) {
            if (!searchControl || !searchToggleBtn) return;
            searchControl.classList.add("open");
            searchToggleBtn.setAttribute("aria-expanded", "true");
            if (focusInput && searchInput) { searchInput.focus(); searchInput.select(); }
            applyAgSearch();
          }
          function closeAgSearch(clearValue) {
            if (!searchControl || !searchToggleBtn) return;
            if (clearValue && searchInput) searchInput.value = "";
            searchControl.classList.remove("open");
            searchToggleBtn.setAttribute("aria-expanded", "false");
            applyAgSearch();
          }
          if (searchToggleBtn) searchToggleBtn.addEventListener("click", function () { (searchControl && searchControl.classList.contains("open")) ? closeAgSearch(false) : openAgSearch(true); });
          if (searchCloseBtn) searchCloseBtn.addEventListener("click", function () { closeAgSearch(true); });
          if (searchInput) searchInput.addEventListener("input", applyAgSearch);

          window.openAgenciaEdit = function (id) {
            var el = document.getElementById("ag-modal-" + id);
            if (el) el.classList.add("open");
            document.body.style.overflow = "hidden";
          };
          window.closeAgenciaEdit = function (id, ev) {
            if (ev && ev.target && ev.target !== ev.currentTarget) return;
            var el = document.getElementById("ag-modal-" + id);
            if (el) el.classList.remove("open");
            document.body.style.overflow = "";
          };

          function setSidebarCollapsed(collapsed) {
            var sb = document.getElementById("sidebar");
            if (!sb) return;
            sb.classList.toggle("collapsed", collapsed);
            localStorage.setItem("sidebar_collapsed", collapsed ? "1" : "0");
          }
          window.toggleSidebar = function () {
            var sb = document.getElementById("sidebar");
            if (!sb) return;
            setSidebarCollapsed(!sb.classList.contains("collapsed"));
          };
          window.addEventListener("DOMContentLoaded", function () {
            setSidebarCollapsed(localStorage.getItem("sidebar_collapsed") === "1");
            applyAgSearch();
          });
          document.addEventListener("keydown", function (e) {
            if ((e.key || "") === "Escape") {
              document.querySelectorAll(".agModalOverlay.open").forEach(function (n) { n.classList.remove("open"); });
              document.body.style.overflow = "";
            }
            if (!(e.ctrlKey || e.metaKey)) return;
            if ((e.key || "").toLowerCase() !== "f") return;
            e.preventDefault();
            openAgSearch(true);
          }, true);
        })();
      </script>
    """)
    html.append("</main></div>")
    return "\n".join(html)
