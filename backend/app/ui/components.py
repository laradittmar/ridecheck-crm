import html as html_lib


def render_whatsapp_icon_svg(size: int = 18, class_name: str = "") -> str:
    px = max(12, int(size or 18))
    extra_class = f" {class_name.strip()}" if class_name and class_name.strip() else ""

    return (
        f'<svg class="icon-only icon-whatsapp{extra_class}" '
        f'viewBox="0 0 24 24" width="{px}" height="{px}" '
        f'aria-hidden="true" focusable="false" style="display:block">'
        
        # WhatsApp bubble (official geometry, outline only)
        '<path d="M12 4.5a7.5 7.5 0 0 0-6.52 11.16L4.5 19.5l3.9-1.02A7.5 7.5 0 1 0 12 4.5z" '
        'fill="none" stroke="currentColor" stroke-width="1.8" '
        'stroke-linecap="round" stroke-linejoin="round"/>'
        
        # Official handset glyph (correct proportions, centered)
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
