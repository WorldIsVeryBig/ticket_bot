"""KKTIX HTML parser helpers for organizer event pages and entry pages."""

from __future__ import annotations

import json
import re
from html import unescape
from urllib.parse import urlparse

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def _clean_text(value: str) -> str:
    text = unescape(_TAG_RE.sub(" ", value))
    return _WS_RE.sub(" ", text).strip()


def _search(pattern: str, html: str, flags: int = 0) -> str:
    match = re.search(pattern, html, flags)
    return match.group(1) if match else ""


def _search_all(pattern: str, html: str, flags: int = 0) -> list[str]:
    return re.findall(pattern, html, flags)


def _load_event_ld_json(html: str) -> dict:
    script = _search(
        r'<script type="application/ld\+json">\s*(.*?)\s*</script>',
        html,
        re.DOTALL,
    )
    if not script:
        return {}

    try:
        data = json.loads(unescape(script))
    except Exception:
        return {}

    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict) and item.get("@type") == "Event":
                return item
    if isinstance(data, dict) and data.get("@type") == "Event":
        return data
    return {}


def build_registration_url(event_url: str) -> str:
    """
    Convert a KKTIX public event URL into the main registration entry URL.

    Examples:
    - https://carrier.kktix.cc/events/476cd237
    - https://kktix.com/events/476cd237
    """
    parsed = urlparse(event_url)
    match = re.search(r"/events/([^/?#]+)", parsed.path)
    if not match:
        return ""
    slug = match.group(1)
    return f"https://kktix.com/events/{slug}/registrations/new"


def detect_kktix_challenge(html: str) -> bool:
    """Detect the Cloudflare-style entry challenge shown by KKTIX."""
    normalized = html.lower()
    return (
        "just a moment" in normalized
        or "enable javascript and cookies to continue" in normalized
        or "請啟用 javascript 與 cookie 以繼續" in html
        or "請啟用 javascript 和 cookie 以繼續" in html
        or "正在執行安全驗證" in html
        or "安全驗證" in html
        or "window._cf_chl_opt" in normalized
    )


def _parse_registration_ticket_units(html: str) -> list[dict]:
    ticket_matches = list(re.finditer(r'id="ticket_(\d+)"', html))
    if not ticket_matches:
        return []

    units = []
    ticket_list_end = html.find("</div>\n<div class=\"platform-fee-remark-wrapper-ticket")

    for index, match in enumerate(ticket_matches):
        start = match.start()
        if index + 1 < len(ticket_matches):
            end = ticket_matches[index + 1].start()
        else:
            end = ticket_list_end if ticket_list_end > start else len(html)

        block = html[start:end]
        ticket_name = _clean_text(
            _search(r'<span class="ticket-name[^"]*">\s*([^<]+?)\s*(?:<!--|<div)', block, re.DOTALL)
        )
        ticket_label = _clean_text(_search(r'<div class="small[^"]*">(.*?)</div>', block, re.DOTALL))
        price = _clean_text(_search(r'(TWD\$[0-9,]+)', block))
        status = "available"
        if "Sold Out" in block:
            status = "sold_out"
        elif "Temporarily Unavailable" in block:
            status = "temporarily_unavailable"
        elif "Need invitation code" in block:
            status = "invitation_required"

        units.append(
            {
                "ticket_id": match.group(1),
                "name": ticket_name,
                "label": ticket_label,
                "price": price,
                "status": status,
                "selectable": 'ng-click="quantityBtnClick(1)"' in block,
                "has_plus_button": 'class="btn-default plus"' in block,
                "requires_disability_identification": "disability identity" in block.lower(),
            }
        )

    return units


def _extract_field_names(html: str, prefix: str) -> list[str]:
    names = []
    for name in _search_all(r'name="([^"]+)"', html):
        if name.startswith(prefix):
            names.append(name)
    return names


def parse_registration_page(html: str, page_url: str = "") -> dict:
    """
    Parse a live KKTIX registrations/new page.

    This page is an Angular application, not a simple HTML form. The stable
    primitives we can currently rely on are:
    - event info rows
    - progress steps
    - visible ticket units and their availability
    - terms checkbox / seat selection actions
    - queue / recaptcha bootstrapping hints embedded in page scripts
    """
    title = _clean_text(_search(r"<title>(.*?)</title>", html, re.DOTALL))
    info_rows = {
        _clean_text(key): _clean_text(value)
        for key, value in _search_all(
            r"<tr[^>]*>\s*<th[^>]*>(.*?)</th>\s*<td[^>]*>(.*?)</td>\s*</tr>",
            html,
            re.DOTALL,
        )
    }
    progress_steps = [
        _clean_text(label)
        for label in _search_all(
            r'<span class="step[^"]*">\s*\d+\s*</span>\s*(.*?)\s*</span>',
            html,
            re.DOTALL,
        )
    ]
    alerts = [
        _clean_text(message)
        for message in _search_all(
            r'<div class="alert">\s*<span[^>]*>(.*?)</span>',
            html,
            re.DOTALL,
        )
    ]
    actions = [
        _clean_text(label)
        for label in _search_all(
            r'<button[^>]+ng-click="challenge[^"]*"[^>]*>\s*(?:<!--.*?-->)*\s*<span[^>]*>(.*?)</span>',
            html,
            re.DOTALL,
        )
    ]
    queue_host = _search(r'queueApi:\s*\{.*?host:\s*"([^"]+)"', html, re.DOTALL)
    recaptcha_normal = _search(r"sitekeyNormal:\s*'([^']+)'", html)
    recaptcha_advanced = _search(r"sitekeyAdvanced:\s*'([^']+)'", html)
    event_slug = _search(r"/events/([^/]+)/registrations/new", page_url)

    return {
        "title": title,
        "page_url": page_url,
        "event_slug": event_slug,
        "challenge": detect_kktix_challenge(html),
        "is_registration_page": 'id="registrationsNewApp"' in html,
        "event_info": {
            "start_time": info_rows.get("Start Time", ""),
            "event_location": info_rows.get("Event Location", ""),
            "event_host": info_rows.get("Event Host", ""),
            "ticket_types": info_rows.get("Ticket Types", ""),
            "payment_terms": info_rows.get("Payment Terms", ""),
        },
        "progress_steps": progress_steps,
        "alerts": alerts,
        "ticket_units": _parse_registration_ticket_units(html),
        "terms_checkbox_id": _search(
            r'<input\b(?=[^>]*type="checkbox")(?=[^>]*id="([^"]*agree[^"]*)")[^>]*>',
            html,
            re.DOTALL,
        ),
        "action_buttons": actions,

        # 不依賴英文或中文按鈕文字，直接辨識 Angular 動作
        "has_best_available_action": bool(
            re.search(
                r'ng-click=["\']\s*challenge\(\s*1\s*\)\s*["\']',
                html,
            )
        ),
        "has_pick_seat_action": bool(
            re.search(
                r'ng-click=["\']\s*challenge\(\s*\)\s*["\']',
                html,
            )
        ),

        "flags": {
            "logged_in": any("successfully logged in" in item.lower() for item in alerts),
            "requires_mobile_verification": any("mobile verification" in item.lower() for item in alerts),
            "has_seat_map": "arenas-map" in html or 'class="arena-wrapper' in html,
            "has_queue_api": bool(queue_host),
            "protected_by_recaptcha": bool(recaptcha_normal or recaptcha_advanced),
            "credit_card_only": "credit card" in info_rows.get("Payment Terms", "").lower(),
        },
        "queue_host": queue_host,
        "recaptcha": {
            "normal_sitekey": recaptcha_normal,
            "advanced_sitekey": recaptcha_advanced,
        },
        "endpoints": {
            "queue_create_order": f"https://{queue_host}/queue/{event_slug}"
            if queue_host and event_slug
            else "",
        },
    }


def parse_order_page(html: str, page_url: str = "") -> dict:
    """
    Parse a reserved KKTIX order page reached after queue / seat selection.

    This page represents the pending order state before the final form submit.
    """
    title = _clean_text(_search(r"<title>(.*?)</title>", html, re.DOTALL))
    reserved_notice = _clean_text(
        _search(
            r'<div ng-switch-when="countingDown"[^>]*>(.*?)</div>',
            html,
            re.DOTALL,
        )
    )
    ticket_name = _clean_text(_search(r'<td class="ticket-name[^"]*">(.*?)</td>', html, re.DOTALL))
    seat_info = _clean_text(
        _search(
            r'<td ng-if="hasArena\(\)" class="seat-info[^"]*">.*?<li[^>]*>(.*?)</li>',
            html,
            re.DOTALL,
        )
    )
    price_count = _clean_text(_search(r'<td class="align-right price-count[^"]*">(.*?)</td>', html, re.DOTALL))
    price_total = _clean_text(_search(r'<td class="align-right price-total[^"]*">(.*?)</td>', html, re.DOTALL))
    total_amount = _clean_text(
        _search(
            r"<th class=\"ng-binding\">Total Amount</th>\s*<td[^>]*>(.*?)</td>",
            html,
            re.DOTALL,
        )
    )
    event_slug = _search(r"/events/([^/]+)/registrations/", page_url)
    order_path = _search(r"(https://kktix\.com/events/[^#]+)", page_url)

    return {
        "title": title,
        "page_url": page_url,
        "challenge": detect_kktix_challenge(html),
        "is_order_page": bool(re.search(r"/events/[^/]+/registrations/[^/#]+", page_url)),
        "reserved_notice": reserved_notice,
        "order_summary": {
            "ticket_name": ticket_name,
            "seat_info": seat_info,
            "price_count": price_count,
            "price_total": price_total,
            "total_amount": total_amount,
        },
        "contact_field_names": _extract_field_names(html, "contact["),
        "attendee_field_names": _extract_field_names(html, "attendees["),
        "selectors": {
            "cancel_ticket": "a.reselect-ticket",
            "confirm_form": "[ng-click='confirmOrder()']",
        },
        "flags": {
            "is_reserved_pending": "Your order has been reserved." in html,
            "has_cancel_ticket": "class=\"btn btn-default reselect-ticket" in html,
            "has_confirm_form": "ng-click=\"confirmOrder()\"" in html,
            "has_real_name_fields": "身分證字號" in html or "field_idnumber" in html,
            "shows_seat_information": "Seat Information" in html,
            "supports_public_attendance_toggle": "Show that you've been to this event on public page." in html,
            "supports_org_fan_toggle": "To be a fan of" in html,
        },
        "endpoints": {
            "cancel_leave": f"{order_path}/leave" if order_path else "",
            "confirm_update_iframe": f"{order_path}?X-Requested-With=IFrame" if order_path else "",
            "base_info": f"https://kktix.com/g/events/{event_slug}/base_info" if event_slug else "",
            "register_info": f"https://kktix.com/g/events/{event_slug}/register_info" if event_slug else "",
        },
    }


def parse_event_page(html: str, page_url: str = "") -> dict:
    """
    Parse a public KKTIX organizer event page.

    Returns stable metadata we can use to bootstrap a future KKTIX bot:
    - title / organizer / venue / start_at
    - registration_url
    - ticket offers from ld+json
    - rule flags inferred from the visible description
    """
    ld_event = _load_event_ld_json(html)

    title = _clean_text(
        _search(r"<div class=\"header-title\">\s*<h1>(.*?)</h1>", html, re.DOTALL)
        or _search(r"<title>(.*?)</title>", html, re.DOTALL)
    )
    organizer = _clean_text(
        _search(r"<div class=\"organizers.*?<a [^>]+>(.*?)</a>", html, re.DOTALL)
    )
    start_at = _clean_text(
        _search(r"<span class=\"timezoneSuffix\">(.*?)</span>", html, re.DOTALL)
    )
    venue_line = _clean_text(
        _search(r"<i class=\"fa fa-map-marker\"></i>\s*(.*?)\s*</span>", html, re.DOTALL)
    )
    registration_url = _search(
        r'href="(https://kktix\.com/events/[^"]+/registrations/new)"',
        html,
    )
    if not registration_url and page_url:
        registration_url = build_registration_url(page_url)

    offers = []
    for offer in ld_event.get("offers", []) if isinstance(ld_event, dict) else []:
        if not isinstance(offer, dict):
            continue
        offers.append(
            {
                "name": str(offer.get("name", "")),
                "price": offer.get("price"),
                "currency": str(offer.get("priceCurrency", "")),
                "availability": str(offer.get("availability", "")),
            }
        )

    normalized = _clean_text(html)
    flags = {
        "requires_real_name": "實名" in normalized or "身分證字號" in normalized,
        "requires_membership_number": "Membership Number" in normalized,
        "has_seat_selection": "自行選位" in normalized or "電腦配位" in normalized,
        "requires_phone_verification": "手機號碼及電子郵件地址" in normalized,
        "members_only_entry": "僅接受已完成手機號碼及電子郵件地址驗證之會員購買" in normalized,
        "credit_card_only": "付款方式：信用卡" in normalized or "僅限刷卡" in normalized,
    }

    return {
        "title": title,
        "page_url": page_url or str(ld_event.get("url", "")),
        "registration_url": registration_url,
        "organizer": organizer,
        "start_at": start_at or str(ld_event.get("startDate", "")),
        "venue": venue_line,
        "offers": offers,
        "flags": flags,
        "challenge": detect_kktix_challenge(html),
    }
