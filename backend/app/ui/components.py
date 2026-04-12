import html as html_lib


def render_whatsapp_icon_svg(size: int = 18, class_name: str = "") -> str:
    px = max(12, int(size or 18))
    extra_class = f" {class_name.strip()}" if class_name and class_name.strip() else ""

    return (
        f'<svg class="icon-only icon-whatsapp{extra_class}" '
        f'viewBox="0 0 24 24" width="{px}" height="{px}" '
        f'aria-hidden="true" focusable="false" style="display:block">'
        '<path d="M12 4.5a7.5 7.5 0 0 0-6.52 11.16L4.5 19.5l3.9-1.02A7.5 7.5 0 1 0 12 4.5z" '
        'fill="none" stroke="currentColor" stroke-width="1.8" '
        'stroke-linecap="round" stroke-linejoin="round"/>'
        '<path d="M9.7 9.6c.2-.4.4-.6.8-.5l.7.2c.3.1.5.3.6.6l.1 1c0 .3-.1.6-.3.8l-.3.3c.6 1 1.5 1.9 2.5 2.5l.3-.3c.2-.2.5-.3.8-.3l1 .1c.3 0 .5.2.6.6l.2.7c.1.4-.1.7-.5.9-.8.3-1.6.3-2.5 0-2.2-.9-4.2-2.9-5.1-5.1-.3-.9-.3-1.7 0-2.5z" '
        'fill="none" stroke="currentColor" stroke-width="1.8" '
        'stroke-linecap="round" stroke-linejoin="round"/>'
        "</svg>"
    )


def render_sidebar_nav(
    *,
    icon_board: str,
    icon_calendar: str,
    icon_filter: str,
    icon_prof: str,
    icon_ag: str,
    icon_wa: str,
    filters_href: str = "/table",
    include_wa_debug: bool = False,
) -> str:
    items = [
        ("/kanban", "CRM", icon_board, ""),
        ("/calendar", "Calendario", icon_calendar, ""),
        (filters_href, "Filtros", icon_filter, ""),
        ("/profesionales", "Profesionales", icon_prof, ""),
        ("/agencias", "Agencias", icon_ag, ""),
        ("/whatsapp/inbox", "WhatsApp Inbox", icon_wa, " waNavIcon"),
    ]
    if include_wa_debug:
        items.append(("/integrations/whatsapp/debug", "WhatsApp Debug", "DBG", ""))
    links = "".join(
        f'<a href="{html_lib.escape(href, quote=True)}"><span class="navIcon{extra_cls}">{icon}</span><span class="navLabel">{html_lib.escape(label)}</span></a>'
        for href, label, icon, extra_cls in items
    )
    return f'<div class="nav">{links}</div>'


def render_sidebar_ai_block() -> str:
    return """
      <div class="sidebarAiBlock" data-ai-control="1">
        <div class="sidebarAiTitle">IA</div>
        <div class="sidebarAiSubtitle">Respuesta + Cotizador + Turnero</div>
        <button
          type="button"
          class="sidebarAiToggle is-loading"
          data-ai-toggle="1"
          role="switch"
          aria-checked="false"
          aria-label="Activar o desactivar IA"
          disabled
        >
          <span class="sidebarAiGlyph sidebarAiGlyphOff" aria-hidden="true">&#10005;</span>
          <span class="sidebarAiGlyph sidebarAiGlyphOn" aria-hidden="true">&#10003;</span>
          <span class="sidebarAiKnob" aria-hidden="true"></span>
        </button>
        <div class="sidebarAiStatus" data-ai-status="1" aria-live="polite"></div>
      </div>
    """


def render_sidebar_ai_script() -> str:
    return """
      <script>
        (function () {
          function setAiToggleState(toggle, enabled, loading) {
            toggle.classList.toggle("is-on", !!enabled);
            toggle.classList.toggle("is-off", !enabled);
            toggle.classList.toggle("is-loading", !!loading);
            toggle.setAttribute("aria-checked", enabled ? "true" : "false");
            toggle.disabled = !!loading;
          }

          function initSidebarAi() {
            var toggle = document.querySelector("[data-ai-toggle='1']");
            if (!toggle) return;

            var block = toggle.closest("[data-ai-control='1']");
            var status = block ? block.querySelector("[data-ai-status='1']") : null;
            var currentValue = null;
            var inFlight = false;

            function setStatus(message, isError) {
              if (!status) return;
              status.textContent = message || "";
              status.classList.toggle("is-error", !!isError);
            }

            function applyValue(nextValue, loading) {
              currentValue = !!nextValue;
              setAiToggleState(toggle, currentValue, loading);
            }

            function loadState() {
              setStatus("", false);
              setAiToggleState(toggle, false, true);
              fetch("/api/settings/ai-enabled", {
                method: "GET",
                headers: { "Accept": "application/json" },
                credentials: "same-origin"
              })
                .then(function (resp) {
                  if (!resp.ok) throw new Error("load_failed");
                  return resp.json();
                })
                .then(function (data) {
                  applyValue(!!(data && data.ai_enabled), false);
                })
                .catch(function () {
                  applyValue(false, false);
                  setStatus("No se pudo cargar", true);
                });
            }

            toggle.addEventListener("click", function () {
              if (inFlight || currentValue === null) return;

              var previousValue = currentValue;
              var nextValue = !previousValue;
              inFlight = true;
              applyValue(nextValue, true);
              setStatus("", false);

              fetch("/api/settings/ai-enabled", {
                method: "PATCH",
                headers: {
                  "Accept": "application/json",
                  "Content-Type": "application/json"
                },
                credentials: "same-origin",
                body: JSON.stringify({ ai_enabled: nextValue })
              })
                .then(function (resp) {
                  if (!resp.ok) throw new Error("patch_failed");
                  return resp.json();
                })
                .then(function (data) {
                  applyValue(!!(data && data.ai_enabled), false);
                })
                .catch(function () {
                  applyValue(previousValue, false);
                  setStatus("No se pudo actualizar", true);
                })
                .finally(function () {
                  inFlight = false;
                });
            });

            loadState();
          }

          if (document.readyState === "loading") {
            document.addEventListener("DOMContentLoaded", initSidebarAi, { once: true });
          } else {
            initSidebarAi();
          }
        })();
      </script>
    """
