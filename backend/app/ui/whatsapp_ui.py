from __future__ import annotations

import html as html_lib
import json
import logging
from datetime import date, datetime, timezone
from urllib import error, request as urlrequest

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import Lead, WhatsAppContact, WhatsAppMessage, WhatsAppThread
from ..schemas.whatsapp_api import WhatsAppThreadOut
from ..services.db_errors import commit_or_400
from ..services.whatsapp_threads import load_thread_payload
from ..settings import get_settings
from .components import render_sidebar_nav, render_whatsapp_icon_svg
from .kanban_view import _base_css, _sidebar_user_block

router = APIRouter(tags=["ui"])
logger = logging.getLogger(__name__)

QUICK_REPLIES = [
    "Gracias por tu mensaje.",
    "En breve te respondemos.",
    "¿Podrías enviarme más detalles?",
    "Te paso el presupuesto enseguida.",
    "Perfecto, agendamos revisión.",
]


class WhatsAppSendPayload(BaseModel):
    text: str
    reply_to_message_id: int | None = None


class WhatsAppDisplayNamePayload(BaseModel):
    display_name: str


class WhatsAppReactionPayload(BaseModel):
    emoji: str


def _build_test_wa_message_id(ts: datetime) -> str:
    return f"wamid.TEST_{int(ts.timestamp())}"


def _send_whatsapp_cloud_text(to_wa_id: str, text: str) -> tuple[str, int]:
    settings = get_settings()
    token = (settings.whatsapp_token or "").strip()
    phone_number_id = (settings.whatsapp_phone_number_id or "").strip()
    logger.info("WHATSAPP_OUTBOUND_ATTEMPT to=%s", to_wa_id)

    if not token or token.lower() == "dummy":
        logger.info("WHATSAPP_OUTBOUND_RESPONSE status=200 mode=dummy")
        return _build_test_wa_message_id(datetime.now(timezone.utc)), 200

    if not phone_number_id:
        raise RuntimeError("WHATSAPP_PHONE_NUMBER_ID missing")

    endpoint = f"https://graph.facebook.com/v19.0/{phone_number_id}/messages"
    payload = {
        "messaging_product": "whatsapp",
        "to": to_wa_id,
        "type": "text",
        "text": {"body": text},
    }
    req = urlrequest.Request(
        endpoint,
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )
    logger.info("WHATSAPP_OUTBOUND_META endpoint=%s", endpoint)
    try:
        with urlrequest.urlopen(req, timeout=15) as resp:
            status_code = int(getattr(resp, "status", 200))
            body = resp.read().decode("utf-8", errors="replace")
    except error.HTTPError as exc:
        err_body = exc.read().decode("utf-8", errors="replace")
        logger.error("WHATSAPP_OUTBOUND_RESPONSE status=%s body=%s", exc.code, err_body)
        raise RuntimeError(f"HTTP {exc.code}: {err_body}") from exc

    logger.info("WHATSAPP_OUTBOUND_RESPONSE status=%s", status_code)
    response_data = json.loads(body) if body.strip() else {}
    messages = response_data.get("messages") if isinstance(response_data, dict) else None
    if isinstance(messages, list) and messages and isinstance(messages[0], dict):
        wa_message_id = str(messages[0].get("id") or "").strip()
        if wa_message_id:
            return wa_message_id, status_code
    raise RuntimeError(f"Unexpected response from WhatsApp Cloud API: {body}")


def _avatar_initials(display_name: str | None) -> str:
    name = (display_name or "").strip()
    if not name:
        return "WA"
    parts = [p for p in name.replace("-", " ").split() if p]
    if not parts:
        return "WA"
    if len(parts) == 1:
        return parts[0][:2].upper()
    return (parts[0][:1] + parts[1][:1]).upper()


def _status_indicator(status: str | None) -> str:
    normalized = str(status or "").strip().lower()
    if normalized == "sent":
        return "✓"
    if normalized in {"delivered", "read"}:
        return "✓✓"
    if normalized == "failed":
        return "failed"
    if normalized == "pending":
        return "pending"
    return normalized or "-"


def _message_text_preview(text: str | None) -> str | None:
    preview = (text or "").replace("\r", " ").replace("\n", " ").strip()
    return preview or None


def _latest_thread_message(db: Session, thread_id: int):
    return db.execute(
        select(WhatsAppMessage)
        .where(WhatsAppMessage.thread_id == thread_id)
        .order_by(WhatsAppMessage.timestamp.desc(), WhatsAppMessage.id.desc())
        .limit(1)
    ).scalars().first()


def _wa_background_style_attr(request: Request) -> str:
    # Replace app/static/wa_empty_bg.jpg and app/static/wa_chat_bg.png to customize WhatsApp backgrounds.
    empty_bg_url = html_lib.escape(str(request.url_for("static", path="wa_empty_bg.jpg")), quote=True)
    empty_png_url = html_lib.escape(str(request.url_for("static", path="wa_empty_bg-03.png")), quote=True)
    chat_bg_url = html_lib.escape(str(request.url_for("static", path="wa_chat_bg.png")), quote=True)
    return (
        f' style="--wa-empty-bg:url(\'{empty_bg_url}\'); '
        f"--wa-empty-state-art:url('{empty_png_url}'), url('{empty_bg_url}'); "
        f"--wa-chat-bg:url('{chat_bg_url}');\""
    )


def _render_list_item(
    thread_id: int,
    wa_id: str,
    display_name: str | None,
    lead_id: int | None,
    unread_count: int,
    last_message_at,
    preview: str,
    active_thread_id: int | None = None,
) -> str:
    name = (display_name or "").strip() or (wa_id or "-")
    item_ts = last_message_at.strftime("%H:%M") if last_message_at else "-"
    item_preview = (preview or "-").replace("\r", " ").replace("\n", " ")
    active_cls = " wa-chat-item-active" if active_thread_id is not None and thread_id == active_thread_id else ""
    unread = int(unread_count or 0)
    badge = f'<span class="wa-chat-unread">{unread}</span>' if unread > 0 else ""
    lead_attr = "" if lead_id is None else str(int(lead_id))
    return (
        f'<div class="wa-chat-row{active_cls}" data-wa-item="1" data-thread-id="{thread_id}" data-lead-id="{html_lib.escape(lead_attr, quote=True)}" '
        f'data-wa-id="{html_lib.escape(wa_id, quote=True)}" data-display-name="{html_lib.escape(name, quote=True)}" data-wa-unread="{unread}" '
        f'data-wa-text="{html_lib.escape((name + " " + wa_id + " " + item_preview).lower(), quote=True)}">'
        f'  <a class="wa-chat-item{active_cls}" href="/whatsapp/thread/{thread_id}">'
        f'    <span class="wa-avatar" aria-hidden="true">{html_lib.escape(_avatar_initials(display_name))}</span>'
        f'    <span class="wa-chat-main">'
        f'      <span class="wa-chat-top">'
        f'        <span class="wa-chat-name">{html_lib.escape(name)}</span>'
        f'        <span class="wa-chat-time">{html_lib.escape(item_ts)}</span>'
        f"      </span>"
        f'      <span class="wa-chat-bottom">'
        f'        <span class="wa-chat-preview">{html_lib.escape(item_preview or "-")}</span>{badge}'
        f"      </span>"
        f"    </span>"
        f"  </a>"
        f'  <button type="button" class="wa-chat-row-menu-btn" aria-label="Más opciones" data-wa-row-menu="1">'
        f'    <svg viewBox="0 0 24 24" aria-hidden="true"><polyline points="6 9 12 15 18 9"></polyline></svg>'
        f"  </button>"
        f"</div>"
    )


def _render_left_panel(list_items_html: str) -> str:
    return (
        '<aside class="wa-left">'
        '  <div class="wa-left-head">'
        '    <div class="wa-search-wrap">'
        '      <span class="wa-search-icon" aria-hidden="true">'
        '        <svg viewBox="0 0 24 24"><circle cx="11" cy="11" r="7"></circle><path d="M21 21l-4.3-4.3"></path></svg>'
        "      </span>"
        '      <input class="wa-search-input" type="text" placeholder="Buscar chat..." />'
        "    </div>"
        '    <div class="wa-filters">'
        '      <button type="button" class="wa-filter-pill wa-filter-pill-active" data-wa-filter="all">All</button>'
        '      <button type="button" class="wa-filter-pill" data-wa-filter="unread">Unread</button>'
        "    </div>"
        "  </div>"
        f'  <div class="wa-list-scroll">{list_items_html}</div>'
        "</aside>"
    )


def _render_empty_chat_state() -> str:
    return (
        '<div class="wa-empty-wrap">'
        '  <div class="wa-empty-card" aria-hidden="true"></div>'
        "</div>"
    )


def _render_whatsapp_shell(user_email: str, title: str, body_html: str) -> str:
    safe_title = html_lib.escape(title)
    icon_board = '<svg class="icon" viewBox="0 0 24 24"><rect x="3" y="4" width="7" height="7"/><rect x="14" y="4" width="7" height="7"/><rect x="3" y="15" width="7" height="7"/><rect x="14" y="15" width="7" height="7"/></svg>'
    icon_calendar = '<svg class="icon" viewBox="0 0 24 24"><rect x="3" y="5" width="18" height="16" rx="2"/><path d="M8 3v4M16 3v4M3 11h18"/></svg>'
    icon_filter = '<svg class="icon" viewBox="0 0 24 24"><path d="M4 6h16l-6 7v5l-4 2v-7z"/></svg>'
    icon_prof = '<svg class="icon" viewBox="0 0 24 24"><circle cx="12" cy="7" r="4"/><path d="M4 21c1.5-4 14.5-4 16 0"/></svg>'
    icon_ag = '<svg class="icon" viewBox="0 0 24 24"><path d="M3 21h18"/><path d="M5 21V8l7-5 7 5v13"/><path d="M9 12h6"/></svg>'
    icon_wa = render_whatsapp_icon_svg()
    icon_toggle = '<svg class="icon icon-only" viewBox="0 0 24 24"><path d="M15 6l-6 6 6 6"/></svg>'
    css = _base_css(
        extra_css="""
      .waPlaceholder {
        background:transparent;
        border:none;
        box-shadow:none;
        padding:0;
      }
      .waPlaceholder h1 {
        margin:0 0 10px 0;
        font-size:22px;
      }
    """
    )

    html = [css, '<link rel="stylesheet" href="/static/wa.css">', '<div class="layout">']
    sidebar_html = """
      <aside class="sidebar" id="sidebar">
        <div class="brandRow">
          <div class="brandText">RIDECHECK</div>
          <button class="sidebarToggle" type="button" onclick="toggleSidebar()" title="Collapse sidebar">%s</button>
        </div>
        %s
    """ % (
        icon_toggle,
        render_sidebar_nav(
            icon_board=icon_board,
            icon_calendar=icon_calendar,
            icon_filter=icon_filter,
            icon_prof=icon_prof,
            icon_ag=icon_ag,
            icon_wa=icon_wa,
            include_wa_debug=True,
        ),
    )
    html.append(
        sidebar_html
        + _sidebar_user_block(user_email)
        + """
      </aside>
    """
    )
    html.append('<main class="main">')
    html.append(f'<div class="waPlaceholder"><h1 class="wa-top-bar">{safe_title}</h1>{body_html}</div>')
    html.append("</main></div>")
    html.append(
        """
    <script>
      function setSidebarCollapsed(collapsed){
        var sb = document.getElementById("sidebar");
        if (!sb) return;
        sb.classList.toggle("collapsed", !!collapsed);
      }
      function toggleSidebar() {
        var sb = document.getElementById("sidebar");
        if (!sb) return;
        var collapsed = !sb.classList.contains("collapsed");
        setSidebarCollapsed(collapsed);
        localStorage.setItem("sidebar_collapsed", collapsed ? "1" : "0");
      }
      function wireWaList(app) {
        var searchInput = app.querySelector(".wa-search-input");
        var filterBtns = app.querySelectorAll("[data-wa-filter]");
        var activeFilter = "all";
        function norm(v){
          return (v || "").toString().toLowerCase().normalize("NFD").replace(/[\\u0300-\\u036f]/g, "").trim();
        }
        function apply(){
          var q = norm(searchInput ? searchInput.value : "");
          app.querySelectorAll("[data-wa-item='1']").forEach(function(item){
            var txt = norm(item.getAttribute("data-wa-text"));
            var unread = parseInt(item.getAttribute("data-wa-unread") || "0", 10);
            var showFilter = (activeFilter === "all") || (unread > 0);
            var showSearch = (!q) || (txt.indexOf(q) !== -1);
            item.style.display = (showFilter && showSearch) ? "" : "none";
          });
        }
        if (searchInput) searchInput.addEventListener("input", apply);
        filterBtns.forEach(function(btn){
          btn.addEventListener("click", function(){
            activeFilter = btn.getAttribute("data-wa-filter") || "all";
            filterBtns.forEach(function(b){ b.classList.remove("wa-filter-pill-active"); });
            btn.classList.add("wa-filter-pill-active");
            apply();
          });
        });
        apply();
      }
      function wireQuickReplies(app){
        var quickWrap = app.querySelector(".wa-quick-wrap");
        if (!quickWrap) return;
        var target = app.querySelector("#wa-quick-target");
        var status = app.querySelector("#wa-quick-status");
        var list = app.querySelector(".wa-quick-list");
        var menuBtn = app.querySelector(".wa-menu-btn");
        var menu = app.querySelector(".wa-menu");
        var findBtn = app.querySelector(".wa-head-search-btn");
        var findWrap = app.querySelector(".wa-find-wrap");
        var findInput = app.querySelector(".wa-find-input");
        var findClose = app.querySelector(".wa-find-close");
          var findResults = app.querySelector(".wa-find-results");
          var contactNameBtn = app.querySelector(".wa-thread-title");
          var contactPanel = app.querySelector(".wa-contact-panel");
          var contactPanelClose = app.querySelector(".wa-contact-panel-close");
          var contactPanelSearch = app.querySelector(".wa-contact-panel-search-btn");
          var contactDeleteBtn = app.querySelector("[data-wa-thread-delete]");
          var contactMuteState = app.querySelector(".wa-thread-muted-state");
          var modal = app.querySelector(".wa-reply-modal");
        var modalTitle = app.querySelector(".wa-reply-title");
        var dialog = app.querySelector(".wa-reply-dialog");
        var editor = app.querySelector(".wa-reply-editor");
        var editList = app.querySelector(".wa-reply-edit-list");
        var saveBtn = app.querySelector(".wa-reply-save");
        var cancelBtn = app.querySelector(".wa-reply-cancel");
        var createBtn = app.querySelector("[data-wa-action='create']");
        var manageBtn = app.querySelector("[data-wa-action='manage']");
        var formatBtns = app.querySelectorAll("[data-wa-cmd]");
        var REPLIES_KEY = "quickReplies";
        var defaultReplies = [];
        try { defaultReplies = JSON.parse(app.getAttribute("data-wa-default-replies") || "[]"); } catch (e) { defaultReplies = []; }
        var replies = [];
        try {
          replies = JSON.parse(localStorage.getItem(REPLIES_KEY) || "[]");
          if (!Array.isArray(replies)) replies = [];
        } catch (e) { replies = []; }
        if (!replies.length) replies = defaultReplies.slice();
        var editIndex = -1;
        var findLastIdx = -1;
        var findLastQ = "";
        var findMatches = [];
        var rightPane = app.querySelector(".wa-right");

        function saveReplies(){ localStorage.setItem(REPLIES_KEY, JSON.stringify(replies)); }
        function setStatus(msg){ if (status) status.textContent = msg || ""; }
        function norm(v){
          return (v || "").toString().toLowerCase().normalize("NFD").replace(/[\u0300-\u036f]/g, "").trim();
        }
        function copyQuickText(txt){
          if (target) target.value = txt;
          if (navigator.clipboard && navigator.clipboard.writeText) {
            navigator.clipboard.writeText(txt).then(function(){ setStatus("Copiado"); }).catch(function(){ setStatus("Listo"); });
          } else setStatus("Listo");
        }
        function renderQuickChips(){
          if (!list) return;
          list.innerHTML = "";
          replies.forEach(function(txt){
            var btn = document.createElement("button");
            btn.type = "button";
            btn.className = "wa-quick-chip";
            btn.setAttribute("data-wa-quick-reply", txt);
            btn.textContent = txt;
            btn.addEventListener("click", function(){ copyQuickText(txt); });
            list.appendChild(btn);
          });
        }
        function renderEditList(){
          if (!editList) return;
          editList.innerHTML = "";
          replies.forEach(function(txt, idx){
            var btn = document.createElement("button");
            btn.type = "button";
            btn.className = "wa-reply-item";
            btn.textContent = txt;
            btn.addEventListener("click", function(){ editIndex = idx; if (editor) editor.innerText = txt; });
            editList.appendChild(btn);
          });
        }
        function openModal(mode){
          if (!modal) return;
          modal.classList.add("wa-open");
          if (dialog) dialog.setAttribute("data-mode", mode || "create");
          if (modalTitle) modalTitle.textContent = mode === "manage" ? "Editar respuestas rápidas" : "Crear respuesta rápida";
          if (mode === "create") { editIndex = -1; if (editor) editor.innerText = ""; }
          else {
            renderEditList();
            if (replies.length) {
              editIndex = 0;
              if (editor) editor.innerText = replies[0];
            } else {
              editIndex = -1;
              if (editor) editor.innerText = "";
            }
          }
          if (editor) editor.focus();
          if (menu) menu.classList.remove("wa-open");
        }
        function closeModal(){ if (modal) modal.classList.remove("wa-open"); }
        function focusBubble(el){
          if (!el) return;
          el.scrollIntoView({ behavior: "smooth", block: "center" });
          el.classList.add("wa-msg-hit");
          setTimeout(function(){ el.classList.remove("wa-msg-hit"); }, 1000);
        }
        function renderFindResults(q){
          if (!findResults) return;
          var rows = Array.prototype.slice.call(app.querySelectorAll(".wa-msg-row"));
          findResults.innerHTML = "";
          findMatches = [];
          if (!q) {
            findResults.innerHTML = '<div class="wa-find-empty">Escribe para buscar mensajes.</div>';
            return;
          }
          var nq = norm(q);
          rows.forEach(function(row, idx){
            var bubble = row.querySelector(".wa-msg-bubble");
            if (!bubble) return;
            var textEl = bubble.querySelector(".wa-msg-text");
            var txt = (textEl ? textEl.textContent : bubble.textContent || "").trim();
            if (norm(txt).indexOf(nq) !== -1) {
              findMatches.push(idx);
              var btn = document.createElement("button");
              btn.type = "button";
              btn.className = "wa-find-result";
              var preview = txt.length > 140 ? (txt.slice(0, 140) + "…") : txt;
              var direction = (row.getAttribute("data-wa-direction") || "").toLowerCase() === "out" ? "Enviado" : "Recibido";
              var timeText = row.getAttribute("data-wa-time") || "";
              var dateText = row.getAttribute("data-wa-date") || "";
              var meta = direction + (timeText ? (" · " + timeText) : "") + (dateText ? (" · " + dateText) : "");
              var textSpan = document.createElement("span");
              textSpan.className = "wa-find-result-text";
              textSpan.textContent = preview;
              var metaSpan = document.createElement("span");
              metaSpan.className = "wa-find-result-meta";
              metaSpan.textContent = meta;
              btn.appendChild(textSpan);
              btn.appendChild(metaSpan);
              btn.addEventListener("click", function(){
                toggleFind(false);
                focusBubble(bubble);
              });
              findResults.appendChild(btn);
            }
          });
          if (!findMatches.length) findResults.innerHTML = '<div class="wa-find-empty">Sin resultados.</div>';
        }
        function refreshContactMuteState(){
          if (!contactMuteState) return;
          var threadId = app.getAttribute("data-wa-current-thread-id") || "";
          contactMuteState.textContent = waIsMuted(threadId) ? "Silenciadas" : "Habilitadas";
        }
        function setContactPanelOpen(open){
          if (!contactPanel) return;
          var isOpen = !!open;
          contactPanel.classList.toggle("wa-open", isOpen);
          if (rightPane) rightPane.classList.toggle("wa-contact-open", isOpen);
          if (isOpen) refreshContactMuteState();
        }
        function toggleContactPanel(forceOpen){
          if (!contactPanel) return;
          var open = typeof forceOpen === "boolean" ? forceOpen : !contactPanel.classList.contains("wa-open");
          if (open && findWrap) findWrap.classList.remove("wa-open");
          if (open && rightPane) rightPane.classList.remove("wa-search-open");
          setContactPanelOpen(open);
        }
        function closeContactPanel(){ setContactPanelOpen(false); }
        function toggleFind(forceOpen){
          if (!findWrap) return;
          var open = typeof forceOpen === "boolean" ? forceOpen : !findWrap.classList.contains("wa-open");
          findWrap.classList.toggle("wa-open", open);
          if (open) closeContactPanel();
          if (rightPane) rightPane.classList.toggle("wa-search-open", open);
          if (open && findInput) {
            findInput.focus();
            renderFindResults(findInput.value || "");
          }
          if (!open) {
            findLastIdx = -1;
            findLastQ = "";
            findMatches = [];
          }
        }
        function findNext(){
          if (!findInput) return;
          var q = (findInput.value || "").trim();
          if (!q) return;
          var bubbles = Array.prototype.slice.call(app.querySelectorAll(".wa-msg-bubble"));
          if (!bubbles.length) return;
          var nq = norm(q);
          if (nq !== findLastQ) {
            findLastIdx = -1;
            findLastQ = nq;
            renderFindResults(q);
          }
          if (!findMatches.length) return;
          var currentPos = findMatches.indexOf(findLastIdx);
          var nextPos = currentPos === -1 ? 0 : (currentPos + 1) % findMatches.length;
          var hit = findMatches[nextPos];
          findLastIdx = hit;
          focusBubble(bubbles[hit]);
        }
        function toggleQuickWrap(open){
          var isOpen = quickWrap.classList.contains("wa-open");
          quickWrap.classList.toggle("wa-open", typeof open === "boolean" ? open : !isOpen);
        }

        renderQuickChips();
        renderEditList();
        saveReplies();

        if (menuBtn) menuBtn.addEventListener("click", function(){ if (menu) menu.classList.toggle("wa-open"); });
        if (createBtn) createBtn.addEventListener("click", function(){ openModal("create"); });
        if (manageBtn) manageBtn.addEventListener("click", function(){ openModal("manage"); });
        if (cancelBtn) cancelBtn.addEventListener("click", closeModal);
        if (saveBtn) saveBtn.addEventListener("click", function(){
          var txt = (editor ? editor.innerText : "").trim();
          if (!txt) return;
          if (editIndex >= 0 && editIndex < replies.length) replies[editIndex] = txt; else replies.push(txt);
          saveReplies();
          renderQuickChips();
          renderEditList();
          setStatus("Guardado");
          closeModal();
        });
        formatBtns.forEach(function(btn){
          btn.addEventListener("click", function(){
            var cmd = btn.getAttribute("data-wa-cmd");
            if (!cmd) return;
            try { document.execCommand(cmd, false); } catch (e) {}
            if (editor) editor.focus();
          });
        });
        if (findBtn) findBtn.addEventListener("click", toggleFind);
        if (findClose) findClose.addEventListener("click", function(){ toggleFind(false); });
        if (findInput) findInput.addEventListener("input", function(){
          findLastIdx = -1;
          findLastQ = norm(findInput.value || "");
          renderFindResults(findInput.value || "");
        });
        if (findInput) findInput.addEventListener("keydown", function(e){
          if (e.key === "Enter") { e.preventDefault(); findNext(); }
          if (e.key === "Escape") { toggleFind(false); }
        });
        if (contactNameBtn) {
          contactNameBtn.addEventListener("click", function(){ toggleContactPanel(); });
          contactNameBtn.addEventListener("keydown", function(e){
            if (e.key !== "Enter" && e.key !== " ") return;
            e.preventDefault();
            toggleContactPanel();
          });
        }
        if (contactPanelClose) contactPanelClose.addEventListener("click", closeContactPanel);
        if (contactPanelSearch) {
          contactPanelSearch.addEventListener("click", function(){
            closeContactPanel();
            toggleFind(true);
          });
        }
        if (contactDeleteBtn) {
          contactDeleteBtn.addEventListener("click", function(){
            if (contactDeleteBtn.hasAttribute("disabled")) return;
            if (!window.confirm("¿Seguro que quieres borrar este chat?")) return;
            var threadId = app.getAttribute("data-wa-current-thread-id") || "";
            if (!threadId) return;
            contactDeleteBtn.disabled = true;
            fetch("/whatsapp/thread/" + threadId, { method: "DELETE" })
              .then(function(resp){
                if (!resp.ok) throw new Error("delete_failed");
                return resp.json();
              })
              .then(function(payload){
                closeContactPanel();
                if (payload && payload.redirect_url) {
                  window.location.href = payload.redirect_url;
                  return;
                }
                window.location.href = "/whatsapp/inbox";
              })
              .catch(function(){
                contactDeleteBtn.disabled = false;
                waShowToast(app, "No se pudo borrar el chat");
              });
          });
        }
        document.addEventListener("click", function(e){
          var targetEl = e.target;
          if (menu && menuBtn && !menu.contains(targetEl) && !menuBtn.contains(targetEl)) menu.classList.remove("wa-open");
        });
        document.addEventListener("keydown", function(e){
          if ((e.key || "") === "/" && !e.ctrlKey && !e.altKey && !e.metaKey) { e.preventDefault(); toggleQuickWrap(true); }
          if ((e.key || "") === "Escape") {
            toggleQuickWrap(false);
            if (menu) menu.classList.remove("wa-open");
            if (modal) modal.classList.remove("wa-open");
            toggleFind(false);
            closeContactPanel();
          }
        }, true);
      }
      function wireComposer(app){
        var form = app.querySelector(".wa-compose-form");
        if (!form) return;
        var textarea = form.querySelector(".wa-compose-input");
        var submitBtn = form.querySelector(".wa-compose-send");
        var replyBar = form.querySelector(".wa-reply-preview");
        var replySnippet = form.querySelector(".wa-reply-preview-snippet");
        var replyClose = form.querySelector(".wa-reply-preview-close");
        if (!textarea) return;

        app.__waReplyId = null;
        app.__waReplyText = "";

        function clearReply(){
          app.__waReplyId = null;
          app.__waReplyText = "";
          if (replySnippet) replySnippet.textContent = "";
          if (replyBar) replyBar.classList.remove("wa-open");
        }
        function setReply(replyId, text){
          var clean = (text || "").trim();
          app.__waReplyId = (replyId == null || replyId === "") ? null : String(replyId);
          app.__waReplyText = clean;
          if (replySnippet) replySnippet.textContent = clean.length > 96 ? (clean.slice(0, 96) + "…") : clean;
          if (replyBar) replyBar.classList.toggle("wa-open", !!clean);
        }

        app.__waSetReply = setReply;
        app.__waClearReply = clearReply;

        function syncComposerState(){
          var hasText = !!((textarea.value || "").trim());
          form.classList.toggle("wa-has-text", hasText);
          if (submitBtn) submitBtn.disabled = !hasText;
        }
        syncComposerState();
        textarea.addEventListener("input", syncComposerState);
        if (replyClose) replyClose.addEventListener("click", clearReply);
        textarea.addEventListener("keydown", function(e){
          if (e.key === "Enter" && !e.shiftKey) {
            e.preventDefault();
            form.requestSubmit();
          }
        });
        form.addEventListener("submit", function(e){
          e.preventDefault();
          var text = (textarea.value || "").trim();
          if (!text) return;
          if (submitBtn) submitBtn.disabled = true;
          var replyId = app.__waReplyId;
          clearReply();
          fetch(form.action, {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({text: text, reply_to_message_id: replyId ? parseInt(replyId, 10) : null})
          }).then(function(resp){
            if (!resp.ok) throw new Error("send_failed");
            window.location.href = app.getAttribute("data-wa-thread-url") || window.location.href;
          }).catch(function(){
            if (submitBtn) submitBtn.disabled = false;
          });
        });
      }
      function wireMessageMenus(app){
        var msgToggles = app.querySelectorAll("[data-wa-menu-toggle='1']");
        if (!msgToggles.length) return;
        var toast = app.querySelector(".wa-toast");
        function showToast(msg){
          if (!toast) return;
          toast.textContent = msg;
          toast.classList.add("wa-show");
          setTimeout(function(){ toast.classList.remove("wa-show"); }, 1100);
        }
        var menu = document.createElement("div");
        menu.className = "wa-global-menu wa-msg-menu";
        menu.innerHTML = ''
          + '<button type="button" class="wa-global-menu-item wa-msg-menu-item" data-wa-menu-action="reply"><span class="wa-msg-menu-icon" aria-hidden="true"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M10 9l-5 4 5 4"></path><path d="M5 13h9a5 5 0 0 1 5 5"></path></svg></span><span class="wa-msg-menu-label">Reply</span></button>'
          + '<button type="button" class="wa-global-menu-item wa-msg-menu-item" data-wa-menu-action="copy"><span class="wa-msg-menu-icon" aria-hidden="true"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="10" height="10" rx="2"></rect><rect x="5" y="5" width="10" height="10" rx="2"></rect></svg></span><span class="wa-msg-menu-label">Copy</span></button>'
          + '<div class="wa-msg-menu-divider" aria-hidden="true"></div>'
          + '<button type="button" class="wa-global-menu-item wa-msg-menu-item" data-wa-menu-action="react"><span class="wa-msg-menu-icon" aria-hidden="true"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="8"></circle><path d="M9 10h.01"></path><path d="M15 10h.01"></path><path d="M9 15c.8 1 1.9 1.5 3 1.5s2.2-.5 3-1.5"></path></svg></span><span class="wa-msg-menu-label">React</span></button>'
          + '<button type="button" class="wa-global-menu-item wa-msg-menu-item" data-wa-menu-action="forward"><span class="wa-msg-menu-icon" aria-hidden="true"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 9l5 4-5 4"></path><path d="M19 13h-9a5 5 0 0 0-5 5"></path></svg></span><span class="wa-msg-menu-label">Forward</span></button>';
        document.body.appendChild(menu);
        var menuState = { text: "", anchor: null };

        function closeMenu(){
          menu.classList.remove("wa-open");
          menuState.text = "";
          menuState.anchor = null;
        }
        function openMenu(btn){
          menuState.id = (btn.getAttribute("data-wa-msg-id") || "").trim();
          menuState.text = (btn.getAttribute("data-wa-msg-text") || "").trim();
          menuState.anchor = btn;
          menu.style.left = "-9999px";
          menu.style.top = "-9999px";
          menu.classList.add("wa-open");
          var rect = btn.getBoundingClientRect();
          var width = menu.offsetWidth || 160;
          var height = menu.offsetHeight || 120;
          var left = rect.right - width;
          if (left < 8) left = 8;
          if (left + width > window.innerWidth - 8) left = window.innerWidth - width - 8;
          var top = rect.bottom + 6;
          if (top + height > window.innerHeight - 8) top = rect.top - height - 6;
          if (top < 8) top = 8;
          menu.style.left = left + "px";
          menu.style.top = top + "px";
        }

        msgToggles.forEach(function(btn){
          btn.addEventListener("click", function(e){
            e.preventDefault();
            e.stopPropagation();
            if (menu.classList.contains("wa-open") && menuState.anchor === btn) {
              closeMenu();
              return;
            }
            openMenu(btn);
          });
        });

        menu.addEventListener("click", function(e){
          var item = e.target.closest("[data-wa-menu-action]");
          if (!item) return;
          e.preventDefault();
          var action = item.getAttribute("data-wa-menu-action") || "";
          var msgTxt = menuState.text || "";
          if (action === "copy") {
            if (navigator.clipboard && navigator.clipboard.writeText) {
              navigator.clipboard.writeText(msgTxt).then(function(){ showToast("Copiado"); }).catch(function(){ showToast("No se pudo copiar"); });
            } else {
              showToast("No se pudo copiar");
            }
          } else if (action === "reply") {
            if (typeof app.__waSetReply === "function") app.__waSetReply(menuState.id, msgTxt);
            var input = app.querySelector(".wa-compose-input");
            if (input) input.focus();
          } else if (action === "react") {
            if (typeof window.__waOpenReactionPicker === "function") window.__waOpenReactionPicker(app, { id: menuState.id, text: menuState.text, anchor: menuState.anchor }, showToast);
          } else if (action === "forward") {
            if (typeof window.__waOpenForwardModal === "function") window.__waOpenForwardModal(app, { id: menuState.id, text: menuState.text, anchor: menuState.anchor }, showToast);
          } else {
            showToast("Coming soon");
          }
          closeMenu();
        });

        document.addEventListener("click", function(e){
          if (!menu.classList.contains("wa-open")) return;
          if (menu.contains(e.target)) return;
          if (e.target.closest("[data-wa-menu-toggle='1']")) return;
          closeMenu();
        });
        document.addEventListener("keydown", function(e){
          if ((e.key || "") === "Escape") closeMenu();
        });
        var scrollWrap = app.querySelector(".wa-chat-scroll");
        if (scrollWrap) scrollWrap.addEventListener("scroll", closeMenu, { passive: true });
        window.addEventListener("resize", closeMenu, { passive: true });
      }
      function waNorm(v){
        return (v || "").toString().toLowerCase().normalize("NFD").replace(/[\u0300-\u036f]/g, "").trim();
      }
      function waThreadMuteKey(threadId){
        return "waThreadMuted:" + String(threadId || "");
      }
      function waIsMuted(threadId){
        return localStorage.getItem(waThreadMuteKey(threadId)) === "1";
      }
      function waSetMuted(threadId, muted){
        localStorage.setItem(waThreadMuteKey(threadId), muted ? "1" : "0");
      }
      function waShowToast(app, msg){
        var toast = app.querySelector(".wa-toast");
        if (!toast) return;
        toast.textContent = msg;
        toast.classList.add("wa-show");
        setTimeout(function(){ toast.classList.remove("wa-show"); }, 1200);
      }
      function waPlayPing(){
        var Ctx = window.AudioContext || window.webkitAudioContext;
        if (!Ctx) return;
        if (!window.__waAudioCtx) window.__waAudioCtx = new Ctx();
        var ctx = window.__waAudioCtx;
        var now = ctx.currentTime;
        var osc = ctx.createOscillator();
        var gain = ctx.createGain();
        osc.type = "sine";
        osc.frequency.setValueAtTime(880, now);
        gain.gain.setValueAtTime(0.0001, now);
        gain.gain.exponentialRampToValueAtTime(0.06, now + 0.01);
        gain.gain.exponentialRampToValueAtTime(0.0001, now + 0.18);
        osc.connect(gain);
        gain.connect(ctx.destination);
        osc.start(now);
        osc.stop(now + 0.2);
      }
      function waOpenUtilityModal(app, title, bodyHtml){
        var modal = app.querySelector(".wa-utility-modal");
        var dialog = app.querySelector(".wa-utility-dialog");
        if (!modal || !dialog) return null;
        dialog.innerHTML = '<div class="wa-utility-head"><div class="wa-utility-title">' + title + '</div><button type="button" class="wa-utility-close" aria-label="Cerrar">×</button></div>' + bodyHtml;
        modal.classList.add("wa-open");
        var closeBtn = dialog.querySelector(".wa-utility-close");
        if (closeBtn) closeBtn.addEventListener("click", function(){ modal.classList.remove("wa-open"); });
        modal.onclick = function(e){ if (e.target === modal) modal.classList.remove("wa-open"); };
        return { modal: modal, dialog: dialog };
      }
      function wireRowMenus(app){
        var rowButtons = app.querySelectorAll("[data-wa-row-menu='1']");
        if (!rowButtons.length) return;
        var menu = document.createElement("div");
        menu.className = "wa-global-menu wa-row-menu";
        document.body.appendChild(menu);
        var state = { row: null };
        function closeMenu(){
          menu.classList.remove("wa-open");
          state.row = null;
        }
        function openMenu(row, btn){
          state.row = row;
          var leadId = (row.getAttribute("data-lead-id") || "").trim();
          var threadId = row.getAttribute("data-thread-id") || "";
          var muted = waIsMuted(threadId);
          menu.innerHTML = ''
            + (leadId ? '<button type="button" class="wa-global-menu-item" data-wa-row-action="go-lead"><span class="wa-msg-menu-icon" aria-hidden="true"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M10 6H6a2 2 0 0 0-2 2v10a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2v-4"></path><path d="M14 4h6v6"></path><path d="M20 4 10 14"></path></svg></span><span class="wa-msg-menu-label">Ir al lead</span></button><div class="wa-msg-menu-divider" aria-hidden="true"></div>' : '')
            + '<button type="button" class="wa-global-menu-item" data-wa-row-action="mute"><span class="wa-msg-menu-icon" aria-hidden="true"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M11 5 6 9H3v6h3l5 4z"></path><path d="M19 9a4 4 0 0 1 0 6"></path><path d="M16.5 6.5a7.5 7.5 0 0 1 0 11"></path></svg></span><span class="wa-msg-menu-label">' + (muted ? 'Unmute notifications' : 'Mute notifications') + '</span></button>'
            + '<button type="button" class="wa-global-menu-item" data-wa-row-action="rename"><span class="wa-msg-menu-icon" aria-hidden="true"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 21l3-1 11-11-2-2L4 18l-1 3z"></path><path d="M14 4l2 2"></path></svg></span><span class="wa-msg-menu-label">Cambiar nombre</span></button>'
            + '<button type="button" class="wa-global-menu-item" data-wa-row-action="link"><span class="wa-msg-menu-icon" aria-hidden="true"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M10 13a5 5 0 0 0 7.07 0l2.12-2.12a5 5 0 0 0-7.07-7.07L10.7 5.22"></path><path d="M14 11a5 5 0 0 0-7.07 0L4.81 13.12a5 5 0 1 0 7.07 7.07L13.3 18.8"></path></svg></span><span class="wa-msg-menu-label">Linkear a un lead</span></button>'
            + '<div class="wa-msg-menu-divider" aria-hidden="true"></div>'
            + '<button type="button" class="wa-global-menu-item" data-wa-row-action="delete"><span class="wa-msg-menu-icon" aria-hidden="true"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 6h18"></path><path d="M8 6v-2h8v2"></path><path d="M9 10v8"></path><path d="M15 10v8"></path><path d="M6 6l1 14h10l1-14"></path></svg></span><span class="wa-msg-menu-label">Borrar chat</span></button>';
          menu.style.left = "-9999px";
          menu.style.top = "-9999px";
          menu.classList.add("wa-open");
          var rect = btn.getBoundingClientRect();
          var width = menu.offsetWidth || 220;
          var height = menu.offsetHeight || 220;
          var left = Math.max(8, Math.min(window.innerWidth - width - 8, rect.right - width));
          var top = rect.bottom + 6;
          if (top + height > window.innerHeight - 8) top = rect.top - height - 6;
          menu.style.left = left + "px";
          menu.style.top = Math.max(8, top) + "px";
        }
        async function loadLeads(){
          if (!window.__waLeadCache) {
            var resp = await fetch("/leads");
            if (!resp.ok) throw new Error("lead_list_failed");
            window.__waLeadCache = await resp.json();
          }
          return window.__waLeadCache || [];
        }
        async function openLeadLinkModal(row){
          var leadId = (row.getAttribute("data-lead-id") || "").trim();
          var threadId = row.getAttribute("data-thread-id") || "";
          var modalState = waOpenUtilityModal(app, "Linkear a un lead", '<input class="wa-utility-search" type="text" placeholder="Buscar por nombre, teléfono o id"><div class="wa-utility-list"></div><div class="wa-utility-actions"></div>');
          if (!modalState) return;
          var dialog = modalState.dialog;
          var search = dialog.querySelector(".wa-utility-search");
          var list = dialog.querySelector(".wa-utility-list");
          var actions = dialog.querySelector(".wa-utility-actions");
          if (leadId) {
            actions.innerHTML = '<button type="button" class="wa-utility-inline-btn">Unlink</button>';
            actions.querySelector("button").addEventListener("click", function(){
              fetch("/whatsapp/thread/" + threadId + "/unlink-lead", { method: "POST" }).then(function(resp){
                if (!resp.ok) throw new Error("unlink_failed");
                window.location.reload();
              }).catch(function(){ waShowToast(app, "No se pudo unlinkear"); });
            });
          }
          var leads = await loadLeads();
          function render(){
            var q = waNorm(search ? search.value : "");
            var items = leads.filter(function(lead){
              var text = [lead.id, lead.nombre, lead.apellido, lead.telefono].join(" ");
              return !q || waNorm(text).indexOf(q) !== -1;
            }).slice(0, 40);
            list.innerHTML = items.map(function(lead){
              return '<button type="button" class="wa-utility-item" data-lead-id="' + lead.id + '"><div>#' + lead.id + ' · ' + (lead.nombre || '-') + ' ' + (lead.apellido || '') + '</div><div class="wa-utility-meta">' + (lead.telefono || '-') + '</div></button>';
            }).join("") || '<div class="wa-utility-meta">Sin resultados.</div>';
            list.querySelectorAll("[data-lead-id]").forEach(function(btn){
              btn.addEventListener("click", function(){
                fetch("/whatsapp/thread/" + threadId + "/link-lead", {
                  method: "POST",
                  headers: { "Content-Type": "application/json" },
                  body: JSON.stringify({ lead_id: parseInt(btn.getAttribute("data-lead-id"), 10) })
                }).then(function(resp){
                  if (!resp.ok) throw new Error("link_failed");
                  window.location.reload();
                }).catch(function(){ waShowToast(app, "No se pudo linkear"); });
              });
            });
          }
          if (search) search.addEventListener("input", render);
          render();
        }
        rowButtons.forEach(function(btn){
          btn.addEventListener("click", function(e){
            e.preventDefault();
            e.stopPropagation();
            var row = btn.closest(".wa-chat-row");
            if (!row) return;
            if (menu.classList.contains("wa-open") && state.row === row) { closeMenu(); return; }
            openMenu(row, btn);
          });
        });
        menu.addEventListener("click", function(e){
          var item = e.target.closest("[data-wa-row-action]");
          if (!item || !state.row) return;
          var action = item.getAttribute("data-wa-row-action");
          var row = state.row;
          var threadId = row.getAttribute("data-thread-id") || "";
          var leadId = (row.getAttribute("data-lead-id") || "").trim();
          var displayName = row.getAttribute("data-display-name") || "";
          closeMenu();
          if (action === "go-lead" && leadId) {
            window.location.href = "/kanban?highlight_lead_id=" + encodeURIComponent(leadId);
            return;
          }
          if (action === "mute") {
            var nextMuted = !waIsMuted(threadId);
            waSetMuted(threadId, nextMuted);
            waShowToast(app, nextMuted ? "Chat muteado" : "Mute desactivado");
            return;
          }
          if (action === "rename") {
            var nextName = window.prompt("Cambiar nombre", displayName);
            if (!nextName) return;
            fetch("/whatsapp/thread/" + threadId + "/display-name", {
              method: "PATCH",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ display_name: nextName })
            }).then(function(resp){
              if (!resp.ok) throw new Error("rename_failed");
              window.location.reload();
            }).catch(function(){ waShowToast(app, "No se pudo cambiar el nombre"); });
            return;
          }
          if (action === "link") {
            openLeadLinkModal(row).catch(function(){ waShowToast(app, "No se pudo abrir el buscador"); });
            return;
          }
          if (action === "delete") {
            if (!window.confirm("¿Borrar este chat localmente?")) return;
            fetch("/whatsapp/thread/" + threadId, { method: "DELETE" }).then(function(resp){
              if (!resp.ok) throw new Error("delete_failed");
              window.location.href = "/whatsapp/inbox";
            }).catch(function(){ waShowToast(app, "No se pudo borrar el chat"); });
          }
        });
        document.addEventListener("click", function(e){
          if (!menu.classList.contains("wa-open")) return;
          if (menu.contains(e.target)) return;
          if (e.target.closest("[data-wa-row-menu='1']")) return;
          closeMenu();
        });
        window.addEventListener("resize", closeMenu, { passive: true });
      }
      function wireComposerExtras(app){
        var form = app.querySelector(".wa-compose-form");
        if (!form) return;
        var textarea = form.querySelector(".wa-compose-input");
        var plusBtn = form.querySelector(".wa-compose-plus");
        var emojiBtn = form.querySelector(".wa-compose-emoji-btn");
        var attachMenu = app.querySelector(".wa-attach-menu");
        var emojiPanel = app.querySelector(".wa-emoji-panel");
        var fileInput = form.querySelector(".wa-file-input");
        var threadId = app.getAttribute("data-wa-current-thread-id") || "";
        var emojis = ["😀","😁","😂","🤣","😊","😍","😘","😉","😎","🤔","🙌","🙏","👍","👎","❤️","🔥","✨","🎉","😮","😢","😡","👏","🤝","👌","🤗","😅","😴","🥳","🤩","😇","🙈","🙉","🙊","💪","📍","🚗","🕒","📞","✅","❌","⚠️","💬","📲","😃","😄","😆","🙂","🙃","😋","😌","😭","😤","🤯","🤍","💙","💚","💛","🧡","💜","🤎"];
        function closePanels(){
          if (attachMenu) attachMenu.classList.remove("wa-open");
          if (emojiPanel) emojiPanel.classList.remove("wa-open");
        }
        if (emojiPanel) {
          emojiPanel.innerHTML = '<input type="text" placeholder="Buscar emoji"><div class="wa-emoji-grid"></div>';
          var search = emojiPanel.querySelector("input");
          var grid = emojiPanel.querySelector(".wa-emoji-grid");
          function renderEmojiGrid(){
            var q = waNorm(search ? search.value : "");
            var items = emojis.filter(function(emoji){ return !q || emoji.indexOf(q) !== -1; });
            grid.innerHTML = items.map(function(emoji){ return '<button type="button" data-wa-emoji="' + emoji + '">' + emoji + '</button>'; }).join("");
            grid.querySelectorAll("[data-wa-emoji]").forEach(function(btn){
              btn.addEventListener("click", function(){
                var emoji = btn.getAttribute("data-wa-emoji") || "";
                if (!textarea) return;
                var start = textarea.selectionStart || textarea.value.length;
                var end = textarea.selectionEnd || textarea.value.length;
                textarea.setRangeText(emoji, start, end, "end");
                textarea.dispatchEvent(new Event("input", { bubbles: true }));
                textarea.focus();
              });
            });
          }
          if (search) search.addEventListener("input", renderEmojiGrid);
          renderEmojiGrid();
        }
        if (plusBtn) plusBtn.addEventListener("click", function(e){
          e.preventDefault();
          if (attachMenu) attachMenu.classList.toggle("wa-open");
          if (emojiPanel) emojiPanel.classList.remove("wa-open");
        });
        if (emojiBtn) emojiBtn.addEventListener("click", function(e){
          e.preventDefault();
          if (emojiPanel) emojiPanel.classList.toggle("wa-open");
          if (attachMenu) attachMenu.classList.remove("wa-open");
        });
        if (attachMenu) attachMenu.querySelectorAll("[data-wa-attach-kind]").forEach(function(btn){
          btn.addEventListener("click", function(){
            if (!fileInput) return;
            var kind = btn.getAttribute("data-wa-attach-kind") || "document";
            fileInput.value = "";
            fileInput.accept = kind === "media" ? "image/*,video/*" : ".pdf,.doc,.docx,.txt,.xls,.xlsx";
            fileInput.setAttribute("data-wa-kind", kind);
            fileInput.click();
          });
        });
        if (fileInput) fileInput.addEventListener("change", function(){
          if (!fileInput.files || !fileInput.files[0] || !threadId) return;
          var file = fileInput.files[0];
          var kind = fileInput.getAttribute("data-wa-kind") || "document";
          var mediaType = kind === "media" ? ((file.type || "").indexOf("video/") === 0 ? "video" : "image") : "document";
          var fd = new FormData();
          fd.append("file", file);
          fd.append("caption", "");
          fd.append("media_type", mediaType);
          fetch("/whatsapp/thread/" + threadId + "/send-media", { method: "POST", body: fd })
            .then(function(resp){ if (!resp.ok) throw new Error("media_failed"); waShowToast(app, "Archivo enviado"); })
            .catch(function(){ waShowToast(app, "No se pudo enviar"); });
        });
        document.addEventListener("click", function(e){
          if (attachMenu && (attachMenu.contains(e.target) || (plusBtn && plusBtn.contains(e.target)))) return;
          if (emojiPanel && (emojiPanel.contains(e.target) || (emojiBtn && emojiBtn.contains(e.target)))) return;
          closePanels();
        });
        var lastMessageId = parseInt(app.getAttribute("data-wa-last-message-id") || "0", 10) || 0;
        if (threadId) {
          window.setInterval(function(){
            fetch("/whatsapp/thread/" + threadId + "/latest").then(function(resp){
              if (!resp.ok) return null;
              return resp.json();
            }).then(function(payload){
              if (!payload || !payload.message_id) return;
              var nextId = parseInt(payload.message_id, 10) || 0;
              if (nextId <= lastMessageId) return;
              lastMessageId = nextId;
              if ((payload.direction || "") === "in" && !waIsMuted(threadId)) waPlayPing();
              window.setTimeout(function(){ window.location.reload(); }, 120);
            }).catch(function(){});
          }, 4000);
        }
      }
      window.__waOpenReactionPicker = function(app, menuState, showToast){
        var picker = document.createElement("div");
        picker.className = "wa-reaction-picker wa-open";
        ["👍","❤️","😂","😮","😢","🙏"].forEach(function(emoji){
          var btn = document.createElement("button");
          btn.type = "button";
          btn.textContent = emoji;
          btn.addEventListener("click", function(){
            var threadId = app.getAttribute("data-wa-current-thread-id") || "";
            fetch("/whatsapp/thread/" + threadId + "/messages/" + menuState.id + "/react", {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ emoji: emoji })
            }).then(function(resp){
              if (!resp.ok) throw new Error("react_failed");
              var row = app.querySelector('[data-wa-message-id="' + menuState.id + '"] .wa-msg-bubble-wrap');
              if (row) {
                var badge = row.querySelector("[data-wa-msg-reaction='1']");
                if (!badge) {
                  badge = document.createElement("div");
                  badge.className = "wa-msg-reaction";
                  badge.setAttribute("data-wa-msg-reaction", "1");
                  row.appendChild(badge);
                }
                badge.textContent = emoji;
              }
            }).catch(function(){ if (showToast) showToast("No se pudo reaccionar"); })
              .finally(function(){ if (picker.parentNode) picker.parentNode.removeChild(picker); });
          });
          picker.appendChild(btn);
        });
        document.body.appendChild(picker);
        var rect = menuState.anchor.getBoundingClientRect();
        picker.style.left = Math.max(8, rect.left - 20) + "px";
        picker.style.top = Math.max(8, rect.top - 56) + "px";
        window.setTimeout(function(){
          document.addEventListener("click", function onDocClick(e){
            if (picker.contains(e.target)) return;
            document.removeEventListener("click", onDocClick, true);
            if (picker.parentNode) picker.parentNode.removeChild(picker);
          }, true);
        }, 0);
      };
      window.__waOpenForwardModal = function(app, menuState, showToast){
        var rows = Array.prototype.slice.call(app.querySelectorAll(".wa-chat-row"));
        var modalState = waOpenUtilityModal(app, "Forward", '<input class="wa-utility-search" type="text" placeholder="Buscar chat"><div class="wa-utility-list"></div><textarea class="wa-utility-search" rows="2" placeholder="Add a message (optional)"></textarea><div class="wa-utility-actions"><button type="button" class="wa-primary">Forward</button></div>');
        if (!modalState) return;
        var dialog = modalState.dialog;
        var search = dialog.querySelector("input");
        var list = dialog.querySelector(".wa-utility-list");
        var note = dialog.querySelector("textarea");
        var submit = dialog.querySelector(".wa-primary");
        var selected = {};
        function render(){
          var q = waNorm(search ? search.value : "");
          var items = rows.filter(function(row){
            var text = [row.getAttribute("data-display-name"), row.getAttribute("data-wa-id")].join(" ");
            return !q || waNorm(text).indexOf(q) !== -1;
          });
          list.innerHTML = items.map(function(row){
            var threadId = row.getAttribute("data-thread-id");
            return '<button type="button" class="wa-utility-item' + (selected[threadId] ? ' wa-selected' : '') + '" data-thread-id="' + threadId + '"><div>' + (row.getAttribute("data-display-name") || "-") + '</div><div class="wa-utility-meta">' + (row.getAttribute("data-wa-id") || "-") + '</div></button>';
          }).join("") || '<div class="wa-utility-meta">Sin resultados.</div>';
          list.querySelectorAll("[data-thread-id]").forEach(function(btn){
            btn.addEventListener("click", function(){
              var tid = btn.getAttribute("data-thread-id") || "";
              selected[tid] = !selected[tid];
              render();
            });
          });
        }
        if (search) search.addEventListener("input", render);
        if (submit) submit.addEventListener("click", function(){
          var ids = Object.keys(selected).filter(function(key){ return !!selected[key]; });
          if (!ids.length) return;
          Promise.all(ids.map(function(tid){
            var noteText = note && note.value.trim() ? note.value.trim() : "";
            var text = menuState.text + (noteText ? ("\\n\\n" + noteText) : "");
            return fetch("/whatsapp/thread/" + tid + "/send", {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ text: text })
            });
          })).then(function(){ window.location.reload(); }).catch(function(){ if (showToast) showToast("No se pudo reenviar"); });
        });
        render();
      };
      function waInitWarnOnce(message, extra){
        if (window.__waInitWarned) return;
        window.__waInitWarned = true;
        if (typeof console !== "undefined" && console.warn) console.warn(message, extra || "");
      }
      function initWhatsappUi(){
        try {
          setSidebarCollapsed(localStorage.getItem("sidebar_collapsed") === "1");
          var apps = document.querySelectorAll(".wa-app");
          if (!apps.length) {
            waInitWarnOnce("WhatsApp init failed: .wa-app not found");
            return;
          }
          apps.forEach(function(app){
            if (app.__waInitDone) return;
            app.__waInitDone = true;
            try {
              wireWaList(app);
              wireRowMenus(app);
              wireQuickReplies(app);
              wireComposer(app);
              wireComposerExtras(app);
              wireMessageMenus(app);
              var isThreadView = !!(app.getAttribute("data-wa-current-thread-id") || "").trim();
              if (isThreadView && (!app.querySelector(".wa-compose-form") || !app.querySelector(".wa-compose-input"))) {
                waInitWarnOnce("WhatsApp init failed: composer nodes missing", app);
              }
            } catch (appErr) {
              app.__waInitDone = false;
              waInitWarnOnce("WhatsApp init failed: app binding error", appErr);
            }
          });
        } catch (err) {
          waInitWarnOnce("WhatsApp init failed", err);
        }
      }
      if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", initWhatsappUi, { once: true });
      } else {
        initWhatsappUi();
      }
    </script>
    """
    )
    return "".join(html)


@router.get("/whatsapp/inbox", response_class=HTMLResponse)
def whatsapp_inbox(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    rows = []
    preview_by_thread: dict[int, str] = {}
    wa_bg_style = _wa_background_style_attr(request)
    try:
        rows = db.execute(
            select(
                WhatsAppThread.id.label("thread_id"),
                WhatsAppThread.lead_id.label("lead_id"),
                WhatsAppContact.wa_id.label("wa_id"),
                WhatsAppContact.display_name.label("display_name"),
                WhatsAppThread.unread_count.label("unread_count"),
                WhatsAppThread.last_message_at.label("last_message_at"),
            )
            .join(WhatsAppContact, WhatsAppThread.contact_id == WhatsAppContact.id)
            .order_by(WhatsAppThread.last_message_at.desc().nullslast(), WhatsAppThread.id.desc())
        ).all()
        thread_ids = [int(r.thread_id) for r in rows]
        if thread_ids:
            preview_rows = db.execute(
                select(WhatsAppMessage.thread_id, WhatsAppMessage.text)
                .where(WhatsAppMessage.thread_id.in_(thread_ids))
                .order_by(WhatsAppMessage.thread_id.asc(), WhatsAppMessage.timestamp.desc(), WhatsAppMessage.id.desc())
            ).all()
            for p in preview_rows:
                tid = int(p.thread_id)
                if tid not in preview_by_thread:
                    preview_by_thread[tid] = (p.text or "").strip()
    except Exception:
        rows = []
        preview_by_thread = {}

    if rows:
        body_cards = []
        for r in rows:
            tid = int(r.thread_id)
            preview = preview_by_thread.get(tid, "-")
            body_cards.append(
                _render_list_item(
                    thread_id=tid,
                    wa_id=r.wa_id or "-",
                    display_name=r.display_name,
                    lead_id=r.lead_id,
                    unread_count=int(r.unread_count or 0),
                    last_message_at=r.last_message_at,
                    preview=preview,
                    active_thread_id=None,
                )
            )
        body_html = f"""
<div class="wa-app"{wa_bg_style}>
{_render_left_panel("".join(body_cards))}
<section class="wa-right wa-empty-state">{_render_empty_chat_state()}</section>
</div>
"""
    else:
        body_html = f"""
<div class="wa-app"{wa_bg_style}>
{_render_left_panel('<div class="wa-empty">No threads.</div>')}
<section class="wa-right wa-empty-state">{_render_empty_chat_state()}</section>
</div>
"""

    html = _render_whatsapp_shell(
        user_email=getattr(request.state, "user_email", ""),
        title="WhatsApp Inbox",
        body_html=body_html,
    )
    return HTMLResponse(html, media_type="text/html; charset=utf-8")


@router.get("/whatsapp/thread/{thread_id}", response_class=HTMLResponse)
def whatsapp_thread(thread_id: str, request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    safe_thread_id = html_lib.escape(thread_id)
    message_rows = []
    wa_id = "-"
    display_name = "-"
    list_rows = []
    list_preview_by_thread: dict[int, str] = {}
    thread_lead_id = None
    thread_created_at = None
    thread_last_message_at = None
    try:
        tid = int(thread_id)
    except (TypeError, ValueError):
        tid = None

    try:
        list_rows = db.execute(
            select(
                WhatsAppThread.id.label("thread_id"),
                WhatsAppThread.lead_id.label("lead_id"),
                WhatsAppContact.wa_id.label("wa_id"),
                WhatsAppContact.display_name.label("display_name"),
                WhatsAppThread.unread_count.label("unread_count"),
                WhatsAppThread.last_message_at.label("last_message_at"),
            )
            .join(WhatsAppContact, WhatsAppThread.contact_id == WhatsAppContact.id)
            .order_by(WhatsAppThread.last_message_at.desc().nullslast(), WhatsAppThread.id.desc())
        ).all()
        list_thread_ids = [int(r.thread_id) for r in list_rows]
        if list_thread_ids:
            list_preview_rows = db.execute(
                select(WhatsAppMessage.thread_id, WhatsAppMessage.text)
                .where(WhatsAppMessage.thread_id.in_(list_thread_ids))
                .order_by(WhatsAppMessage.thread_id.asc(), WhatsAppMessage.timestamp.desc(), WhatsAppMessage.id.desc())
            ).all()
            for p in list_preview_rows:
                thread_key = int(p.thread_id)
                if thread_key not in list_preview_by_thread:
                    list_preview_by_thread[thread_key] = (p.text or "").strip()
    except Exception:
        list_rows = []
        list_preview_by_thread = {}

    list_html = []
    for r in list_rows:
        item_tid = int(r.thread_id)
        item_preview = list_preview_by_thread.get(item_tid, "-")
        list_html.append(
            _render_list_item(
                thread_id=item_tid,
                wa_id=r.wa_id or "-",
                display_name=r.display_name,
                lead_id=r.lead_id,
                unread_count=int(r.unread_count or 0),
                last_message_at=r.last_message_at,
                preview=item_preview,
                active_thread_id=tid,
            )
        )

    if tid is not None:
        try:
            thread_contact = db.execute(
                select(WhatsAppThread, WhatsAppContact.wa_id, WhatsAppContact.display_name)
                .join(WhatsAppContact, WhatsAppThread.contact_id == WhatsAppContact.id)
                .where(WhatsAppThread.id == tid)
            ).first()
            if thread_contact is not None:
                thread = thread_contact[0]
                wa_id = thread_contact.wa_id or "-"
                display_name = thread_contact.display_name or "-"
                thread_lead_id = thread.lead_id
                thread_created_at = thread.created_at
                thread.unread_count = 0
                db.commit()
            message_rows = db.execute(
                select(
                    WhatsAppMessage.id,
                    WhatsAppMessage.timestamp,
                    WhatsAppMessage.direction,
                    WhatsAppMessage.status,
                    WhatsAppMessage.wa_message_id,
                    WhatsAppContact.wa_id,
                    WhatsAppContact.display_name,
                    WhatsAppMessage.text,
                    WhatsAppMessage.raw_payload,
                )
                .join(WhatsAppThread, WhatsAppMessage.thread_id == WhatsAppThread.id)
                .join(WhatsAppContact, WhatsAppThread.contact_id == WhatsAppContact.id)
                .where(WhatsAppMessage.thread_id == tid)
                .order_by(WhatsAppMessage.timestamp.asc(), WhatsAppMessage.id.asc())
            ).all()
        except Exception:
            db.rollback()
            message_rows = []

    if message_rows:
        thread_last_message_at = message_rows[-1].timestamp

    default_replies_json = html_lib.escape(json.dumps(QUICK_REPLIES, ensure_ascii=False), quote=True)
    wa_bg_style = _wa_background_style_attr(request)
    last_message_id = int(message_rows[-1].id) if message_rows else 0
    thread_created_txt = (thread_created_at.strftime("%d/%m/%Y %H:%M") if thread_created_at else "Sin fecha")
    thread_last_msg_txt = (thread_last_message_at.strftime("%d/%m/%Y %H:%M") if thread_last_message_at else "Sin mensaje")
    if thread_lead_id is not None:
        thread_lead_id_txt = f"#{int(thread_lead_id)}"
    else:
        thread_lead_id_txt = "Sin lead vinculado"

    if message_rows:
        msg_html = []
        text_by_id: dict[int, str] = {}
        today = date.today()
        last_day_key: str | None = None
        for m in message_rows:
            msg_day = m.timestamp.date() if m.timestamp else None
            day_key = msg_day.isoformat() if msg_day else "no-date"
            if day_key != last_day_key:
                if msg_day is None:
                    day_label = "Sin fecha"
                elif msg_day == today:
                    day_label = "Today"
                elif 0 < (today - msg_day).days <= 6:
                    day_label = msg_day.strftime("%A")
                else:
                    day_label = msg_day.strftime("%d/%m/%Y")
                msg_html.append(
                    '<div class="wa-day-sep"><span class="wa-day-pill">'
                    f"{html_lib.escape(day_label)}"
                    "</span></div>"
                )
                last_day_key = day_key
            msg_id = str(m.id or "")
            ts = m.timestamp.strftime("%H:%M") if m.timestamp else "-"
            direction = (m.direction or "").strip().lower()
            side_cls = "wa-msg-out" if direction == "out" else "wa-msg-in"
            msg_text = m.text or "-"
            raw_payload = m.raw_payload if isinstance(m.raw_payload, dict) else {}
            reply_to_raw = raw_payload.get("reply_to_message_id")
            reaction = str(raw_payload.get("local_reaction") or "").strip()
            reply_to_id = None
            try:
                reply_to_id = int(str(reply_to_raw))
            except (TypeError, ValueError):
                reply_to_id = None
            quote_html = ""
            if reply_to_id is not None:
                quoted_text = text_by_id.get(reply_to_id, "").strip()
                if quoted_text:
                    preview = quoted_text if len(quoted_text) <= 96 else (quoted_text[:96] + "…")
                    quote_html = (
                        '<div class="wa-msg-quote">'
                        '<span class="wa-msg-quote-bar" aria-hidden="true"></span>'
                        '<div class="wa-msg-quote-text">'
                        f"{html_lib.escape(preview)}"
                        "</div></div>"
                    )
            status_html = ""
            if direction == "out":
                indicator = _status_indicator(m.status)
                status_html = (
                    f'<span class="wa-msg-status" title="{html_lib.escape(m.status or "-", quote=True)}">'
                    f"{html_lib.escape(indicator)}"
                    "</span>"
                )
            reaction_html = (
                f'<div class="wa-msg-reaction" data-wa-msg-reaction="1">{html_lib.escape(reaction)}</div>'
                if reaction
                else ""
            )
            msg_html.append(
                f'<div class="wa-msg wa-msg-row {side_cls}" data-wa-message-id="{html_lib.escape(msg_id, quote=True)}" data-wa-direction="{html_lib.escape(direction, quote=True)}" data-wa-time="{html_lib.escape(ts, quote=True)}" data-wa-date="{html_lib.escape(day_key, quote=True)}">'
                f'  <div class="wa-msg-bubble-wrap">'
                f'    <div class="wa-msg-bubble">'
                f'      <button type="button" class="wa-msg-menu-btn wa-msg-menu-toggle" aria-label="Message options" data-wa-menu-toggle="1" data-wa-msg-id="{html_lib.escape(msg_id, quote=True)}" data-wa-msg-text="{html_lib.escape(msg_text, quote=True)}"><svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="6 9 12 15 18 9"></polyline></svg></button>'
                f"      {quote_html}"
                f'      <div class="wa-msg-text">{html_lib.escape(msg_text)}</div>'
                f'      <div class="wa-msg-meta"><span class="wa-msg-time">{html_lib.escape(ts)}</span>{status_html}</div>'
                f"    </div>"
                f"    {reaction_html}"
                f'  </div>'
                f"</div>"
            )
            try:
                text_by_id[int(msg_id)] = msg_text
            except (TypeError, ValueError):
                pass
        quick_html = "".join(
            f'<button type="button" class="wa-quick-chip" data-wa-quick-reply="{html_lib.escape(q, quote=True)}">{html_lib.escape(q)}</button>'
            for q in QUICK_REPLIES
        )
        body_html = f"""
<div class="wa-app"{wa_bg_style} data-wa-default-replies="{default_replies_json}" data-wa-thread-url="/whatsapp/thread/{tid}" data-wa-current-thread-id="{tid}" data-wa-last-message-id="{last_message_id}">
{_render_left_panel("".join(list_html))}
<section class="wa-right">
<div class="wa-thread-head">
  <span class="wa-avatar wa-avatar-head">{html_lib.escape(_avatar_initials(display_name))}</span>
  <div class="wa-thread-head-main">
    <div class="wa-thread-title" role="button" tabindex="0" aria-label="Ver información de contacto">{html_lib.escape((display_name or "").strip() or wa_id)}</div>
  </div>
  <button type="button" class="wa-head-icon-btn wa-head-search-btn" aria-label="Buscar en chat">
    <svg viewBox="0 0 24 24"><circle cx="11" cy="11" r="7"></circle><path d="M21 21l-4.3-4.3"></path></svg>
  </button>
  <button type="button" class="wa-head-icon-btn wa-menu-btn" aria-label="Opciones">⋯</button>
  <div class="wa-menu">
    <button type="button" class="wa-menu-item" data-wa-action="create">Crear respuesta rápida</button>
    <button type="button" class="wa-menu-item" data-wa-action="manage">Editar respuestas rápidas</button>
  </div>
</div>
<div class="wa-find-wrap">
  <div class="wa-find-head">
    <div class="wa-find-title">Buscar en conversación</div>
    <button type="button" class="wa-find-close" aria-label="Cerrar búsqueda">×</button>
  </div>
  <div class="wa-find-body">
    <input class="wa-find-input" type="text" placeholder="Buscar y Enter..." />
  </div>
  <div class="wa-find-results"><div class="wa-find-empty">Escribe para buscar mensajes.</div></div>
</div>
  <div class="wa-contact-panel">
    <div class="wa-contact-panel-head">
      <div class="wa-contact-panel-title">Información del contacto</div>
      <button type="button" class="wa-contact-panel-close" aria-label="Cerrar">×</button>
    </div>
    <div class="wa-contact-panel-body">
      <div class="wa-contact-panel-scroll">
        <div class="wa-contact-profile">
          <div class="wa-contact-avatar">{html_lib.escape(_avatar_initials((display_name or "").strip() or wa_id))}</div>
          <div class="wa-contact-name">{html_lib.escape((display_name or "").strip() or wa_id)}</div>
          <div class="wa-contact-phone">{html_lib.escape(wa_id)}</div>
            <div class="wa-contact-actions">
            <button type="button" class="wa-contact-action-btn wa-contact-panel-search-btn" aria-label="Buscar en chat">
              <svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="11" cy="11" r="7"></circle><path d="M21 21l-4.3-4.3"></path></svg>
              <span>Buscar</span>
            </button>
            {'<a class="wa-contact-lead-pill wa-contact-lead-btn" href="/kanban?highlight_lead_id=' + html_lib.escape(str(int(thread_lead_id)), quote=True) + '">' +
              html_lib.escape(str(int(thread_lead_id))) + "</a>" if thread_lead_id is not None else
             '<button type="button" class="wa-contact-lead-pill" aria-disabled="true" disabled>Sin lead vinculado</button>'}
          </div>
        </div>
        <div class="wa-contact-section">
          <div class="wa-contact-section-title">About</div>
          <div class="wa-contact-about-row">
            <span class="wa-contact-about-label">Media, links and docs</span>
            <span class="wa-contact-about-count">0</span>
          </div>
        </div>
        <div class="wa-contact-section">
          <div class="wa-contact-row">
            <span class="wa-contact-row-label">Notificaciones</span>
            <span class="wa-contact-row-value wa-thread-muted-state">-</span>
          </div>
          <div class="wa-contact-row">
            <span class="wa-contact-row-label">Creado</span>
            <span class="wa-contact-row-value">{html_lib.escape(thread_created_txt)}</span>
          </div>
          <div class="wa-contact-row">
            <span class="wa-contact-row-label">Último mensaje</span>
            <span class="wa-contact-row-value">{html_lib.escape(thread_last_msg_txt)}</span>
          </div>
        </div>
      </div>
      <div class="wa-contact-panel-footer">
        <button type="button" class="wa-contact-delete-btn" data-wa-thread-delete>Delete chat</button>
      </div>
    </div>
  </div>
<div class="wa-chat-scroll"><div class="wa-chat">
{''.join(msg_html)}
</div></div>
<div class="wa-quick-wrap">
  <div class="wa-quick-list">{quick_html}</div>
  <input id="wa-quick-target" type="hidden" value="">
  <div id="wa-quick-status" class="wa-quick-status"></div>
</div>
<form class="wa-compose-form" action="/whatsapp/thread/{tid}/send" method="post">
  <div class="wa-reply-preview">
    <span class="wa-reply-preview-bar" aria-hidden="true"></span>
    <div class="wa-reply-preview-body">
      <div class="wa-reply-preview-label">Replying to…</div>
      <div class="wa-reply-preview-snippet"></div>
    </div>
    <button type="button" class="wa-reply-preview-close" aria-label="Cancelar respuesta">×</button>
  </div>
  <div class="wa-compose-box">
    <button type="button" class="wa-compose-aux wa-compose-plus" aria-label="Adjuntar">
      <svg viewBox="0 0 24 24"><path d="M12 5v14"></path><path d="M5 12h14"></path></svg>
    </button>
    <button type="button" class="wa-compose-aux wa-compose-emoji-btn" aria-label="Emoji">
      <svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="8"></circle><path d="M9 10h.01"></path><path d="M15 10h.01"></path><path d="M8.5 14.5c1 1.2 2.2 1.8 3.5 1.8s2.5-.6 3.5-1.8"></path></svg>
    </button>
    <textarea class="wa-compose-input" rows="2" placeholder="Escribe un mensaje"></textarea>
    <input type="file" class="wa-file-input" hidden>
    <button type="submit" class="wa-compose-send" aria-label="Enviar"></button>
  </div>
</form>
<div class="wa-toast" aria-live="polite"></div>
<div class="wa-attach-menu">
  <button type="button" class="wa-attach-item" data-wa-attach-kind="document">Document</button>
  <button type="button" class="wa-attach-item" data-wa-attach-kind="media">Photos &amp; videos</button>
</div>
<div class="wa-emoji-panel"></div>
<div class="wa-utility-modal"><div class="wa-utility-dialog"></div></div>
<div class="wa-reply-modal">
  <div class="wa-reply-dialog" data-mode="create">
    <h4 class="wa-reply-title">Crear respuesta rápida</h4>
    <div class="wa-reply-tools">
      <button type="button" data-wa-cmd="bold"><b>B</b></button>
      <button type="button" data-wa-cmd="italic"><i>I</i></button>
      <button type="button" data-wa-cmd="underline"><u>U</u></button>
    </div>
    <div class="wa-reply-edit-list"></div>
    <div class="wa-reply-editor" contenteditable="true"></div>
    <div class="wa-reply-actions">
      <button type="button" class="wa-reply-save">Guardar</button>
      <button type="button" class="wa-reply-cancel">Cancelar</button>
    </div>
  </div>
</div>
</section></div>
"""
    else:
        body_html = f"""
<div class="wa-app"{wa_bg_style} data-wa-default-replies="{default_replies_json}" data-wa-thread-url="/whatsapp/thread/{tid}" data-wa-current-thread-id="{tid}" data-wa-last-message-id="{last_message_id}">
{_render_left_panel("".join(list_html))}
<section class="wa-right">
<div class="wa-thread-head">
  <span class="wa-avatar wa-avatar-head">{html_lib.escape(_avatar_initials(display_name))}</span>
  <div class="wa-thread-head-main">
    <div class="wa-thread-title" role="button" tabindex="0" aria-label="Ver información de contacto">
      {html_lib.escape((display_name or "").strip() or wa_id)}
    </div>
  </div>
  <button type="button" class="wa-head-icon-btn wa-head-search-btn" aria-label="Buscar en chat">
    <svg viewBox="0 0 24 24"><circle cx="11" cy="11" r="7"></circle><path d="M21 21l-4.3-4.3"></path></svg>
  </button>
  <button type="button" class="wa-head-icon-btn wa-menu-btn" aria-label="Opciones">⋯</button>
  <div class="wa-menu">
    <button type="button" class="wa-menu-item" data-wa-action="create">Crear respuesta rápida</button>
    <button type="button" class="wa-menu-item" data-wa-action="manage">Editar respuestas rápidas</button>
  </div>
</div>
<div class="wa-find-wrap">
  <div class="wa-find-head">
    <div class="wa-find-title">Buscar en conversación</div>
    <button type="button" class="wa-find-close" aria-label="Cerrar búsqueda">×</button>
  </div>
  <div class="wa-find-body">
    <input class="wa-find-input" type="text" placeholder="Buscar y Enter..." />
  </div>
  <div class="wa-find-results"><div class="wa-find-empty">Escribe para buscar mensajes.</div></div>
</div>
  <div class="wa-contact-panel">
    <div class="wa-contact-panel-head">
      <div class="wa-contact-panel-title">Información del contacto</div>
      <button type="button" class="wa-contact-panel-close" aria-label="Cerrar">×</button>
    </div>
    <div class="wa-contact-panel-body">
      <div class="wa-contact-panel-scroll">
        <div class="wa-contact-profile">
          <div class="wa-contact-avatar">{html_lib.escape(_avatar_initials((display_name or "").strip() or wa_id))}</div>
          <div class="wa-contact-name">{html_lib.escape((display_name or "").strip() or wa_id)}</div>
          <div class="wa-contact-phone">{html_lib.escape(wa_id)}</div>
            <div class="wa-contact-actions">
            <button type="button" class="wa-contact-action-btn wa-contact-panel-search-btn" aria-label="Buscar en chat">
              <svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="11" cy="11" r="7"></circle><path d="M21 21l-4.3-4.3"></path></svg>
              <span>Buscar</span>
            </button>
            {'<a class="wa-contact-lead-pill wa-contact-lead-btn" href="/kanban?highlight_lead_id=' + html_lib.escape(str(int(thread_lead_id)), quote=True) + '">' +
              html_lib.escape(str(int(thread_lead_id))) + "</a>" if thread_lead_id is not None else
             '<button type="button" class="wa-contact-lead-pill" aria-disabled="true" disabled>Sin lead vinculado</button>'}
          </div>
        </div>
        <div class="wa-contact-section">
          <div class="wa-contact-section-title">About</div>
          <div class="wa-contact-about-row">
            <span class="wa-contact-about-label">Media, links and docs</span>
            <span class="wa-contact-about-count">0</span>
          </div>
        </div>
        <div class="wa-contact-section">
          <div class="wa-contact-row">
            <span class="wa-contact-row-label">Notificaciones</span>
            <span class="wa-contact-row-value wa-thread-muted-state">-</span>
          </div>
          <div class="wa-contact-row">
            <span class="wa-contact-row-label">Creado</span>
            <span class="wa-contact-row-value">{html_lib.escape(thread_created_txt)}</span>
          </div>
          <div class="wa-contact-row">
            <span class="wa-contact-row-label">Último mensaje</span>
            <span class="wa-contact-row-value">{html_lib.escape(thread_last_msg_txt)}</span>
          </div>
        </div>
      </div>
      <div class="wa-contact-panel-footer">
        <button type="button" class="wa-contact-delete-btn" data-wa-thread-delete>Delete chat</button>
      </div>
    </div>
  </div>
<div class="wa-chat-scroll"><div class="wa-empty">No messages for <code>{safe_thread_id}</code>.</div></div>
<div class="wa-quick-wrap">
  <div class="wa-quick-list"></div>
  <input id="wa-quick-target" type="hidden" value="">
  <div id="wa-quick-status" class="wa-quick-status"></div>
</div>
<form class="wa-compose-form" action="/whatsapp/thread/{tid}/send" method="post">
  <div class="wa-reply-preview">
    <span class="wa-reply-preview-bar" aria-hidden="true"></span>
    <div class="wa-reply-preview-body">
      <div class="wa-reply-preview-label">Replying to…</div>
      <div class="wa-reply-preview-snippet"></div>
    </div>
    <button type="button" class="wa-reply-preview-close" aria-label="Cancelar respuesta">×</button>
  </div>
  <div class="wa-compose-box">
    <button type="button" class="wa-compose-aux wa-compose-plus" aria-label="Adjuntar">
      <svg viewBox="0 0 24 24"><path d="M12 5v14"></path><path d="M5 12h14"></path></svg>
    </button>
    <button type="button" class="wa-compose-aux wa-compose-emoji-btn" aria-label="Emoji">
      <svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="8"></circle><path d="M9 10h.01"></path><path d="M15 10h.01"></path><path d="M8.5 14.5c1 1.2 2.2 1.8 3.5 1.8s2.5-.6 3.5-1.8"></path></svg>
    </button>
    <textarea class="wa-compose-input" rows="2" placeholder="Escribe un mensaje"></textarea>
    <input type="file" class="wa-file-input" hidden>
    <button type="submit" class="wa-compose-send" aria-label="Enviar"></button>
  </div>
</form>
<div class="wa-toast" aria-live="polite"></div>
<div class="wa-attach-menu">
  <button type="button" class="wa-attach-item" data-wa-attach-kind="document">Document</button>
  <button type="button" class="wa-attach-item" data-wa-attach-kind="media">Photos &amp; videos</button>
</div>
<div class="wa-emoji-panel"></div>
<div class="wa-utility-modal"><div class="wa-utility-dialog"></div></div>
<div class="wa-reply-modal">
  <div class="wa-reply-dialog" data-mode="create">
    <h4 class="wa-reply-title">Crear respuesta rápida</h4>
    <div class="wa-reply-tools">
      <button type="button" data-wa-cmd="bold"><b>B</b></button>
      <button type="button" data-wa-cmd="italic"><i>I</i></button>
      <button type="button" data-wa-cmd="underline"><u>U</u></button>
    </div>
    <div class="wa-reply-edit-list"></div>
    <div class="wa-reply-editor" contenteditable="true"></div>
    <div class="wa-reply-actions">
      <button type="button" class="wa-reply-save">Guardar</button>
      <button type="button" class="wa-reply-cancel">Cancelar</button>
    </div>
  </div>
</div>
</section></div>
"""

    html = _render_whatsapp_shell(
        user_email=getattr(request.state, "user_email", ""),
        title="WhatsApp",
        body_html=body_html,
    )
    return HTMLResponse(html, media_type="text/html; charset=utf-8")


@router.post("/whatsapp/thread/{thread_id}/send")
def whatsapp_thread_send(thread_id: int, payload: WhatsAppSendPayload, db: Session = Depends(get_db)):
    text = (payload.text or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="Text is required")

    thread_data = db.execute(
        select(WhatsAppThread, WhatsAppContact.wa_id)
        .join(WhatsAppContact, WhatsAppThread.contact_id == WhatsAppContact.id)
        .where(WhatsAppThread.id == thread_id)
    ).first()
    if thread_data is None:
        raise HTTPException(status_code=404, detail="Thread not found")

    thread = thread_data[0]
    to_wa_id = str(thread_data.wa_id or "").strip()
    if not to_wa_id:
        raise HTTPException(status_code=400, detail="Thread has no wa_id")

    now_utc = datetime.now(timezone.utc)
    outbound = WhatsAppMessage(
        thread_id=thread_id,
        wa_message_id=None,
        direction="out",
        status="pending",
        timestamp=now_utc,
        text=text,
        raw_payload={"reply_to_message_id": payload.reply_to_message_id} if payload.reply_to_message_id else None,
    )
    db.add(outbound)
    thread.last_message_at = now_utc
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        outbound.status = "sent"
        db.add(outbound)
        thread.last_message_at = now_utc
        db.commit()
    db.refresh(outbound)

    try:
        wa_message_id, _ = _send_whatsapp_cloud_text(to_wa_id=to_wa_id, text=text)
        outbound.status = "sent"
        outbound.wa_message_id = wa_message_id
        db.commit()
    except Exception:
        db.rollback()
        outbound.status = "failed"
        db.add(outbound)
        db.commit()
        logger.exception("WHATSAPP_OUTBOUND_SEND_FAILED thread_id=%s", thread_id)

    return RedirectResponse(url=f"/whatsapp/thread/{thread_id}", status_code=303)


@router.patch("/whatsapp/thread/{thread_id}/display-name", response_model=WhatsAppThreadOut)
def whatsapp_thread_display_name(
    thread_id: int,
    payload: WhatsAppDisplayNamePayload,
    db: Session = Depends(get_db),
):
    new_name = (payload.display_name or "").strip()
    if not new_name:
        raise HTTPException(status_code=400, detail="display_name is required")

    thread_data = db.execute(
        select(WhatsAppThread, WhatsAppContact)
        .join(WhatsAppContact, WhatsAppThread.contact_id == WhatsAppContact.id)
        .where(WhatsAppThread.id == thread_id)
    ).first()
    if thread_data is None:
        raise HTTPException(status_code=404, detail="Thread not found")

    contact = thread_data[1]
    contact.display_name = new_name
    commit_or_400(db, detail="No se pudo actualizar el nombre del chat")
    payload_out = load_thread_payload(db, thread_id)
    if payload_out is None:
        raise HTTPException(status_code=404, detail="Thread not found")
    return payload_out


@router.delete("/whatsapp/thread/{thread_id}")
def whatsapp_thread_delete(thread_id: int, db: Session = Depends(get_db)):
    thread = db.get(WhatsAppThread, thread_id)
    if not thread:
        raise HTTPException(status_code=404, detail="Thread not found")
    db.delete(thread)
    commit_or_400(db, detail="No se pudo borrar el chat")
    return {"ok": True, "redirect_url": "/whatsapp/inbox"}


@router.post("/whatsapp/thread/{thread_id}/send-media")
async def whatsapp_thread_send_media(
    thread_id: int,
    file: UploadFile = File(...),
    caption: str = Form(default=""),
    media_type: str = Form(default="document"),
    db: Session = Depends(get_db),
):
    if not db.get(WhatsAppThread, thread_id):
        raise HTTPException(status_code=404, detail="Thread not found")
    _ = await file.read()
    raise HTTPException(status_code=501, detail="El envío de archivos todavía no está implementado")


@router.post("/whatsapp/thread/{thread_id}/messages/{message_id}/react")
def whatsapp_thread_react(
    thread_id: int,
    message_id: int,
    payload: WhatsAppReactionPayload,
    db: Session = Depends(get_db),
):
    emoji = (payload.emoji or "").strip()
    if not emoji:
        raise HTTPException(status_code=400, detail="emoji is required")
    msg = db.execute(
        select(WhatsAppMessage)
        .where(WhatsAppMessage.thread_id == thread_id)
        .where(WhatsAppMessage.id == message_id)
    ).scalars().first()
    if msg is None:
        raise HTTPException(status_code=404, detail="Message not found")
    raw_payload = msg.raw_payload if isinstance(msg.raw_payload, dict) else {}
    raw_payload["local_reaction"] = emoji
    msg.raw_payload = raw_payload
    commit_or_400(db, detail="No se pudo guardar la reacción")
    return {"ok": True, "emoji": emoji, "message_id": message_id}


@router.get("/whatsapp/thread/{thread_id}/latest")
def whatsapp_thread_latest(thread_id: int, db: Session = Depends(get_db)):
    if not db.get(WhatsAppThread, thread_id):
        raise HTTPException(status_code=404, detail="Thread not found")
    latest = _latest_thread_message(db, thread_id)
    if latest is None:
        return {"message_id": None, "direction": None, "timestamp": None}
    return {
        "message_id": latest.id,
        "direction": latest.direction,
        "timestamp": latest.timestamp,
        "text": _message_text_preview(latest.text),
    }
