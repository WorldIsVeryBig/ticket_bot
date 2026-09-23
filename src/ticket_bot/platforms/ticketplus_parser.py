"""Ticket Plus 頁面快照的純解析、選票與訂單核對規則。"""

from __future__ import annotations

import json
import re
from urllib.parse import urlparse

from ticket_bot.config import EventConfig


def normalize_text(value: object) -> str:
    """把 DOM 文字壓成適合比對的單行文字。"""
    return re.sub(r"\s+", " ", str(value or "")).strip()


def extract_activity_id(url: str) -> str:
    parsed = urlparse(url)
    hostname = (parsed.hostname or "").casefold()
    if not (hostname and ("ticketplus.com.tw" in hostname or "ticketplus.com.test" in hostname)):
        return ""
    match = re.fullmatch(r"/activity/([A-Za-z0-9]+?)/?", parsed.path)
    return match.group(1) if match else ""


def is_ticketplus_lab_url(url: str) -> bool:
    """只有 .test 測試網域才走純 API 模擬流程，正式網站走瀏覽器 UI 操作。"""
    parsed = urlparse(url)
    hostname = (parsed.hostname or "").casefold()
    return "ticketplus.com.test" in hostname


def parse_api_document(payload: object) -> dict:
    """統一解析 getS3 字串 JSON 與一般 API envelope。"""
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError as error:
            raise ValueError("Ticket Plus API 回傳的 JSON 格式無效") from error
    if not isinstance(payload, dict):
        raise ValueError("Ticket Plus API 回傳內容不是物件")
    if payload.get("errCode") not in (None, "00"):
        message = normalize_text(payload.get("errMsg") or payload.get("errDetail"))
        raise ValueError(message or f"Ticket Plus API 錯誤: {payload.get('errCode')}")
    result = payload.get("result", payload)
    if not isinstance(result, dict):
        raise ValueError("Ticket Plus API result 不是物件")
    return result


def build_lab_ticket_plan(
    event: EventConfig,
    sessions: list[dict],
    ticket_areas: list[dict],
    products: list[dict],
    live_result: dict,
) -> dict:
    """從靜態設定與即時庫存建立 reserve 所需的最小選票計畫。"""
    normalized_sessions = [
        {
            "key": normalize_text(item.get("sessionId")),
            "name": normalize_text(item.get("name")),
            "date": " ".join(
                filter(
                    None,
                    (
                        normalize_text(item.get("date")),
                        normalize_text(item.get("time")),
                    ),
                )
            ),
            "available": not bool(item.get("hidden")),
            "status": "可售",
        }
        for item in sessions
    ]
    session = select_session(event, normalized_sessions)
    session_id = session["key"]

    live_areas = {
        normalize_text(item.get("id")): item
        for item in live_result.get("ticketArea", [])
        if isinstance(item, dict)
    }
    live_products = {
        normalize_text(item.get("id")): item
        for item in live_result.get("product", [])
        if isinstance(item, dict)
    }
    products_by_area: dict[str, list[dict]] = {}
    for product in products:
        if normalize_text(product.get("sessionId")) != session_id or product.get("hidden"):
            continue
        products_by_area.setdefault(
            normalize_text(product.get("ticketAreaId")), []
        ).append(product)

    keywords = resolve_area_keywords(event) or [""]
    for keyword in keywords:
        for area in ticket_areas:
            area_id = normalize_text(area.get("ticketAreaId"))
            if normalize_text(area.get("sessionId")) != session_id or area.get("hidden"):
                continue
            area_name = normalize_text(area.get("name"))
            if keyword and keyword.casefold() not in area_name.casefold():
                continue
            current_area = live_areas.get(area_id, {})
            if normalize_text(current_area.get("status")).casefold() != "onsale":
                continue
            if _to_int(current_area.get("count"), 0) < event.ticket_count:
                continue
            price = _to_int(current_area.get("price", area.get("price")), -1)
            if price < 0 or (
                event.max_price is not None and price > event.max_price
            ):
                continue
            for product in products_by_area.get(area_id, []):
                product_id = normalize_text(product.get("productId"))
                current_product = live_products.get(product_id, {})
                if normalize_text(current_product.get("status")).casefold() != "onsale":
                    continue
                purchase_limit = _to_int(
                    current_product.get("purchaseLimit"), event.ticket_count
                )
                if purchase_limit < event.ticket_count:
                    continue
                return {
                    "session_id": session_id,
                    "ticket_area_id": area_id,
                    "ticket_name": area_name,
                    "product_id": product_id,
                    "price": price,
                    "quantity": event.ticket_count,
                }
    raise ValueError("Ticket Plus 找不到符合候選順序、票數與價格上限的票種")


def parse_price(value: str) -> int | None:
    text = normalize_text(value)
    if "免費" in text:
        return 0
    matches = re.findall(
        r"(?:NT(?:\$|\.)?|TWD\$?|\$)\s*([0-9][0-9,]*)",
        text,
        re.IGNORECASE,
    )
    return int(matches[-1].replace(",", "")) if matches else None


def ticket_units_from_order_areas(areas: list[dict]) -> list[dict]:
    """把 Ticket Plus 的 Vuetify 票區面板轉成共用選票資料。"""
    units: list[dict] = []
    seen: set[str] = set()
    for area in areas:
        key = normalize_text(area.get("key"))
        text = normalize_text(area.get("text"))
        if not key or key in seen or not text:
            continue
        seen.add(key)
        price = parse_price(text)
        if price is None:
            continue
        name = normalize_text(
            re.split(
                r"\s+(?:剩餘\s*[0-9]+|熱賣中|NT(?:\$|\.)?\s*[0-9])",
                text,
                maxsplit=1,
                flags=re.IGNORECASE,
            )[0]
        )
        remaining_match = re.search(r"剩餘\s*([0-9]+)", text)
        remaining = int(remaining_match.group(1)) if remaining_match else 99
        unavailable = bool(
            area.get("disabled")
            or remaining == 0
            or re.search(r"售完|已售完|完售|不可購買|停止販售", text, re.IGNORECASE)
        )
        units.append(
            {
                "key": key,
                "name": name,
                "price_text": text,
                "price": price,
                "status": "售完" if unavailable else "可售",
                "available": not unavailable,
                "max_quantity": remaining,
                "general_admission": bool(
                    re.search(r"自由入座|自由席|站席|搖滾區|無劃位|一般區", text, re.IGNORECASE)
                ),
                "selection_mode": "area_panel",
            }
        )
    return units


def resolve_area_keywords(event: EventConfig) -> list[str]:
    ordered = event.area_keywords or ([event.area_keyword] if event.area_keyword else [])
    return [normalize_text(value) for value in ordered if normalize_text(value)]


def _session_is_hydrated(session: dict) -> bool:
    text = " ".join(
        normalize_text(session.get(key, ""))
        for key in ("name", "date", "status")
    ).casefold()
    return bool(text) and not any(
        marker in text
        for marker in ("loading", "載入中", "資料載入")
    )


def detect_page_state(snapshot: dict) -> str:
    """依安全優先順序辨識頁面；付款邊界永遠只回報、不操作。"""
    checks = (
        ("login_required", "login_required"),
        ("queue", "queue"),
        ("payment_boundary", "payment_boundary"),
        ("order_review", "order_review"),
        ("seat_map", "seat_map"),
        ("ticket_units", "ticket_select"),
        ("sessions", "session_select"),
        ("sale_not_started", "sale_not_started"),
    )
    for key, state in checks:
        value = snapshot.get(key)
        if key == "sessions" and value:
            value = [session for session in value if _session_is_hydrated(session)]
        if value:
            return state
    return "unknown"


def select_session(event: EventConfig, sessions: list[dict]) -> dict:
    available = [
        item
        for item in sessions
        if item.get("available") and _session_is_hydrated(item)
    ]
    if event.date_keyword:
        keyword = normalize_text(event.date_keyword).casefold()
        keyword_digits = re.sub(r"\D", "", keyword)

        def matches(item: dict) -> bool:
            candidate = " ".join(
                (
                    normalize_text(item.get("date", "")),
                    normalize_text(item.get("name", "")),
                )
            ).casefold()
            if keyword in candidate:
                return True
            candidate_digits = re.sub(r"\D", "", candidate)
            return len(keyword_digits) >= 6 and keyword_digits in candidate_digits

        available = [
            item
            for item in available
            if matches(item)
        ]
    if not available:
        target = event.date_keyword or "第一個可售"
        raise ValueError(f"Ticket Plus 找不到符合日期的可售場次: {target}")
    return available[0]


def _to_int(value: object, default: int) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def build_ticket_plan(event: EventConfig, ticket_units: list[dict]) -> dict:
    for keyword in resolve_area_keywords(event) or [""]:
        for unit in ticket_units:
            name = normalize_text(unit.get("name", ""))
            price = unit.get("price")
            if keyword and keyword.casefold() not in name.casefold():
                continue
            if not unit.get("available") or _to_int(unit.get("max_quantity"), 0) < event.ticket_count:
                continue
            if event.max_price is not None and (
                not isinstance(price, int) or price > event.max_price
            ):
                continue
            return {
                "ticket_key": unit["key"],
                "ticket_name": name,
                "price": price,
                "quantity": event.ticket_count,
                "general_admission": bool(unit.get("general_admission")),
                "selection_mode": unit.get("selection_mode", "quantity"),
            }
    raise ValueError("Ticket Plus 找不到符合候選順序、票數與價格上限的票種")


def select_adjacent_seats(event: EventConfig, seats: list[dict]) -> list[dict]:
    for keyword in resolve_area_keywords(event) or [""]:
        groups: dict[tuple[str, str], list[tuple[int, dict]]] = {}
        for seat in seats:
            area = normalize_text(seat.get("area", ""))
            if keyword and keyword.casefold() not in area.casefold():
                continue
            if not seat.get("available") or not seat.get("selectable"):
                continue
            price = seat.get("price")
            if event.max_price is not None and (
                not isinstance(price, int) or price > event.max_price
            ):
                continue
            number_text = normalize_text(seat.get("seat_number", ""))
            if not number_text.isdigit():
                continue
            group_key = (area, normalize_text(seat.get("row", "")))
            groups.setdefault(group_key, []).append((int(number_text), seat))

        for group in groups.values():
            ordered = sorted(group, key=lambda item: item[0])
            for start in range(len(ordered) - event.ticket_count + 1):
                window = ordered[start : start + event.ticket_count]
                if all(
                    window[index + 1][0] == window[index][0] + 1
                    for index in range(len(window) - 1)
                ):
                    return [item[1] for item in window]
    raise ValueError("Ticket Plus 找不到足夠的同排相鄰座位")


def validate_order_review(event: EventConfig, plan: dict, review: dict) -> list[str]:
    errors: list[str] = []
    price = _to_int(plan.get("price"), -1)
    quantity = _to_int(plan.get("quantity"), -1)
    expected_subtotal = price * quantity
    if normalize_text(review.get("ticket_name")) != normalize_text(plan.get("ticket_name")):
        errors.append("訂單票種與選擇票種不符")
    if _to_int(review.get("quantity"), -1) != quantity:
        errors.append("訂單張數與選擇張數不符")
    subtotal = _to_int(review.get("subtotal"), -1)
    if subtotal != expected_subtotal:
        errors.append(f"票款小計 {review.get('subtotal')} 與預期票款 {expected_subtotal} 不符")
    fees = _to_int(review.get("fees"), 0)
    total = _to_int(review.get("total"), -1)
    if subtotal < 0 or total < 0 or total != subtotal + fees:
        errors.append("訂單總額與票款小計加附加費用不符")
    if event.max_price is not None and price > event.max_price:
        errors.append("單張票價超過 max_price")
    return errors