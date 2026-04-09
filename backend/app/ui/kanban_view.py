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
from .components import render_sidebar_nav, render_whatsapp_icon_svg

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
        <div class="sidebarUser">{safe_email}</div>
        <form method="post" action="/logout">
          <button class="logoutBtn" type="submit">Log Out</button>
        </form>
      </div>
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
      }}
      .sidebar.collapsed{{ width:68px; }}
      .brandRow{{ display:flex; align-items:center; justify-content:space-between; gap:8px; margin-bottom:12px; }}
      .brandText{{ font-weight:700; font-size:12px; letter-spacing:.3px; color:#e5e7eb; }}
      .sidebarToggle{{ border:none; background:transparent; color:#e5e7eb; cursor:pointer; padding:4px; border-radius:8px; }}
      .sidebarToggle:hover{{ background: rgba(255,255,255,.08); }}
      .nav a{{
        display:flex; align-items:center; gap:10px; padding:10px 10px; border-radius:10px; color:#e5e7eb; text-decoration:none; margin-bottom:8px;
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
      .sidebarFooter {{
        margin-top:auto;
        padding-top:12px;
        border-top:1px solid rgba(255,255,255,.16);
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
      @media (max-width: 520px) {{
        .revEditPanel .grid {{
          grid-template-columns: 1fr;
        }}
      }}
      .pill {{ display:inline-flex; align-items:center; padding:4px 8px; border-radius:999px; white-space:nowrap; font-size:var(--chip-font); font-weight:700; border:1px solid var(--border); background:#f9fafb; min-width:0; max-width:100%; overflow:hidden; text-overflow:ellipsis; }}
      .pill-veh {{ background:#eef2ff; border-color:#c7d2fe; }}
      .pill-count {{ background:#ecfeff; border-color:#a5f3fc; }}
      .pill-prof {{ background:#ecfdf3; border-color:#86efac; color:#166534; }}
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
]

FLAG_LABELS = {
    "PRESUPUESTANDO": "Presupuestando",
    "PRESUPUESTO_ENVIADO": "Presupuesto enviado",
    "ACEPTADO": "Aceptado",
    "RECOMPRA": "Re-compra",
    "PERDIDO": "Perdido",
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
            f'<div class="kanban-column kanbanCol" data-estado="{estado_k}" data-estado-label="{estado_label}"><h2><span>{estado_label}</span> '
            f'<span class="badge">{len(col)}</span> {header_actions}</h2>'
        )
        for l in col:
            html.append(render_lead_card(l, zones_map, profesionales=profesionales or [], agencias=agencias or []))
        html.append("</div>")

    html.append("</div>")  # board
    html.append('<div id="popover-root"></div>')
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
      .tableWrap { overflow: auto; background: rgba(255,255,255,.7); border: 1px solid var(--border); border-radius: 14px; box-shadow: var(--shadow); max-height: calc(100vh - 160px); }
      table { width: 100%; border-collapse: collapse; min-width: 1100px; }
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
        labels = ["Lun", "Mar", "Mi-", "Jue", "Vie", "S-b", "Dom"]
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

    calendar_css = """
      .calGrid { display:grid; grid-template-columns: repeat(7, minmax(0, 1fr)); gap:10px; }
      .calCol { background: rgba(255,255,255,.7); border: 1px solid var(--border); border-radius: 14px; padding: 10px; box-shadow: var(--shadow); min-height: 140px; }
      .calHead { font-weight:700; font-size:12px; color:#111827; margin-bottom:8px; display:flex; justify-content:space-between; align-items:center; }
      .calAppt { display:block; text-decoration:none; color:inherit; background:#fff; border:1px solid var(--border); border-radius:12px; padding:8px; margin-bottom:8px; box-shadow: var(--shadow); }
      .calAppt:hover { box-shadow: var(--shadow2); }
      .calAppt.past { background:#f3f4f6; border-color:#d1d5db; color:#6b7280; }
      .calAppt.future-ok { background:#ecfdf3; border-color:#86efac; }
      .calAppt.future-pending { background:#fee2e2; border-color:#fca5a5; }
      .calMeta { font-size:12px; color: var(--muted); }
      .calRow { display:flex; justify-content:space-between; gap:8px; align-items:center; min-width:0; }
      .calMetaVehicle { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 100%; }
      .calTopBar { display:flex; align-items:center; justify-content:space-between; gap:10px; flex-wrap:wrap; margin-bottom:12px; }
      .calTopCenter { font-weight:700; }
      .calTopLinks { display:flex; gap:8px; align-items:center; flex-wrap:wrap; }
      @media (max-width: 900px) { .calGrid { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
      @media (max-width: 520px) { .calGrid { grid-template-columns: 1fr; } }
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
    html.append(f"""
      <div class="calTopBar">
        <div class="calTopLinks">
          <a class="btn btn-sm" href="/calendar?week={prev_monday.isoformat()}">{ICON_ARROW_LEFT} Semana anterior</a>
        </div>
        <div class="calTopCenter">Semana {week_start.strftime("%d/%m/%Y")} a {week_end.strftime("%d/%m/%Y")}</div>
        <div class="calTopLinks">
          <a class="btn btn-sm" href="/calendar?week={next_monday.isoformat()}">Semana siguiente {ICON_ARROW_RIGHT}</a>
          <a class="btn btn-sm" href="/calendar">Hoy</a>
        </div>
      </div>
    """)
    html.append('<div class="calGrid" style="margin-top:12px;" data-search-scope="calendar">')

    for i in range(7):
        day = week_start + timedelta(days=i)
        appts = by_day.get(day, [])
        html.append(f'<div class="calCol"><div class="calHead"><span>{day_label(day)}</span><span class="muted">{len(appts)}</span></div>')
        if not appts:
            html.append('<div class="muted small">Sin turnos.</div>')
        else:
            for it in appts:
                l = it["lead"]
                r = it["rev"]
                n = f"{(_get(l,'nombre') or '').strip()} {(_get(l,'apellido') or '').strip()}".strip() or "-"
                veh = " / ".join([x for x in [
                    _val(r.tipo_vehiculo),
                    _val(r.marca),
                    _val(r.modelo),
                    str(r.anio) if r.anio else "",
                ] if x])
                time_txt = r.turno_hora.strftime("%H:%M") if r.turno_hora else "-"
                addr = _url_link(r.link_maps) if _safe_url(r.link_maps) else _txt(r.direccion_texto)
                estado_op = _txt(r.estado_revision)
                prof_id = getattr(r, "profesional_id", None)
                prof = prof_by_id.get(prof_id) if prof_id else None
                prof_label = _profesional_label(prof) if prof else "-"
                href = f"/kanban?open_lead={l.id}&open_rev={r.id}"
                search_text = html_lib.escape(" ".join([
                    _val(n),
                    _val(veh),
                    _val(r.direccion_texto),
                    _val(prof_label),
                    _val(estado_op),
                ]), quote=True)
                appt_dt = None
                if r.turno_fecha:
                    if r.turno_hora:
                        appt_dt = datetime.combine(r.turno_fecha, r.turno_hora)
                    else:
                        appt_dt = datetime.combine(r.turno_fecha, time.min)
                is_past = appt_dt is not None and appt_dt < now
                estado_val = (r.estado_revision or "").strip().upper()
                if is_past:
                    appt_cls = "past"
                else:
                    appt_cls = "future-ok" if estado_val == "CONFIRMADO" else "future-pending"
                html.append(f"""
                    <a class="calAppt {appt_cls}" href="{href}" data-search="{search_text}">
                      <div class="calRow"><b>{_txt(n)}</b><span class="pill">{time_txt}</span></div>
                      <div class="calMeta calMetaVehicle">Vehículo: {_txt(veh)}</div>
                      <div class="calMeta">Dirección: {addr}</div>
                      <div class="calMeta">Profesional: {_txt(prof_label)}</div>
                      <div class="calMeta">Estado operativo: {_txt(estado_op)}</div>
                    </a>
                  """)
        html.append("</div>")

    html.append("</div>")
    html.append("""
      <script>
        (function () {
          var searchControl = document.getElementById("calendar-search-control");
          var searchInput = document.getElementById("calendar-search-input");
          var searchToggleBtn = document.getElementById("calendar-search-toggle");
          var searchCloseBtn = document.getElementById("calendar-search-close");
          var searchCount = document.getElementById("calendar-search-count");
          var searchScope = document.querySelector('[data-search-scope="calendar"]');
          function normalizeSearchText(value) {
            return (value || "")
              .toString()
              .normalize("NFD")
              .replace(/[\\u0300-\\u036f]/g, "")
              .toLowerCase()
              .trim();
          }
          function applyCalendarSearch() {
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
              var fallbackCards = searchScope.querySelectorAll(".calAppt");
              fallbackCards.forEach(function (node) {
                total += 1;
                var haystack = normalizeSearchText(node.textContent || "");
                var show = !q || haystack.indexOf(q) !== -1;
                node.classList.toggle("search-item-hidden", !show);
                if (show) visible += 1;
              });
            }
            if (searchCount) {
              searchCount.textContent = q ? (visible + " / " + total) : (total + " / " + total);
            }
          }
          function openCalendarSearch(focusInput) {
            if (!searchControl || !searchToggleBtn) return;
            searchControl.classList.add("open");
            searchToggleBtn.setAttribute("aria-expanded", "true");
            if (focusInput && searchInput) {
              searchInput.focus();
              searchInput.select();
            }
            applyCalendarSearch();
          }
          function closeCalendarSearch(clearValue) {
            if (!searchControl || !searchToggleBtn) return;
            if (clearValue && searchInput) searchInput.value = "";
            searchControl.classList.remove("open");
            searchToggleBtn.setAttribute("aria-expanded", "false");
            applyCalendarSearch();
          }
          if (searchToggleBtn) {
            searchToggleBtn.addEventListener("click", function () {
              var isOpen = searchControl && searchControl.classList.contains("open");
              if (isOpen) {
                closeCalendarSearch(false);
                return;
              }
              openCalendarSearch(true);
            });
          }
          if (searchCloseBtn) {
            searchCloseBtn.addEventListener("click", function () {
              closeCalendarSearch(true);
            });
          }
          if (searchInput) {
            searchInput.addEventListener("input", applyCalendarSearch);
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
            applyCalendarSearch();
            if (searchInput && (searchInput.value || "").trim()) {
              openCalendarSearch(false);
            }
          });
          document.addEventListener("keydown", function (e) {
            if (!(e.ctrlKey || e.metaKey)) return;
            if ((e.key || "").toLowerCase() !== "f") return;
            e.preventDefault();
            openCalendarSearch(true);
          }, true);
        })();
      </script>
    """)
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
        vehicle_badge = last_rev.tipo_vehiculo.replace("_", " ").title()

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
        chunks.append('<div class="muted" style="margin-top:10px;">No hay revisiones a-n.</div>')
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

          <form method="post" action="/ui/revision_latest_update" class="revEditPanel">
            <input type="hidden" name="lead_id" value="{lead_id}"/>
            <div class="revModalBody">
              <style>
                #editrev-{lead_id} .revSection {{ margin-bottom:12px; }}
                #editrev-{lead_id} .revSection > legend {{ font-size:13px; font-weight:800; color:#111827; padding:0 4px; }}
                #editrev-{lead_id} .revSectionBody {{ margin-top:8px; }}
                #editrev-{lead_id} .revReadonly {{ background:#f3f4f6; color:#4b5563; }}
                #editrev-{lead_id} .revHint {{ margin-top:6px; }}
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
                      <input type="date" name="turno_fecha" value="{tf_val}"/>
                    </div>
                    <div>
                      <div class="label">Turno hora</div>
                      <input type="time" name="turno_hora" value="{th_val}"/>
                    </div>
                  </div>

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
          var sel = root.querySelector('select[data-tipo-vendedor="1"]');
          var wrap = root.querySelector('[data-agencia-wrap="1"]');
          var agenciaSelect = root.querySelector('select[data-agencia-select="1"]');
          var recalcSel = root.querySelector('select[data-recalcular-presupuesto="1"]');
          var totalInput = root.querySelector('input[data-precio-total="1"]');
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
          if (sel) sel.addEventListener("change", syncAgencia);
          if (recalcSel) recalcSel.addEventListener("change", syncPrecioTotalReadonly);
          syncAgencia();
          syncPrecioTotalReadonly();
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
