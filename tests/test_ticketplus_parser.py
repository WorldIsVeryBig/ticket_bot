"""Ticket Plus 純資料解析與選擇規則測試。"""

from ticket_bot.config import EventConfig
from ticket_bot.platforms.ticketplus_parser import (
    build_ticket_plan,
    detect_page_state,
    extract_activity_id,
    parse_price,
    resolve_area_keywords,
    select_adjacent_seats,
    select_session,
    ticket_units_from_order_areas,
    validate_order_review,
)


def make_event(**overrides):
    values = {
        "name": "Ticket Plus",
        "platform": "ticketplus",
        "url": "https://ticketplus.com.tw/activity/6d2868aeec40f3676950edc8fb70a1f4",
        "ticket_count": 2,
        "date_keyword": "2026-11-22",
        "area_keyword": "",
        "area_keywords": ["S區", "一般區"],
        "max_price": 4000,
    }
    values.update(overrides)
    return EventConfig(**values)


def test_extract_activity_id_rejects_other_hosts():
    assert extract_activity_id(make_event().url) == "6d2868aeec40f3676950edc8fb70a1f4"
    assert extract_activity_id("https://example.com/activity/abc") == ""


def test_parse_price_accepts_ticketplus_copy():
    assert parse_price("S區 NT$4,000") == 4000
    assert parse_price("一般區 NT2,500") == 2500
    assert parse_price("免費") == 0


def test_nested_order_areas_only_expose_leaf_ticket_areas():
    areas = [
        {
            "key": "price-4880",
            "text": "4880 區 A1區 熱賣中 NT.4,880 A2區 熱賣中 NT.4,880",
            "disabled": False,
            "is_leaf": False,
            "ancestor_keys": [],
        },
        {
            "key": "area-a1",
            "text": "A1區 熱賣中 NT.4,880",
            "disabled": False,
            "is_leaf": True,
            "ancestor_keys": ["price-4880"],
        },
    ]

    units = ticket_units_from_order_areas(areas)

    assert [unit["name"] for unit in units] == ["A1區"]
    assert units[0]["ancestor_keys"] == ["price-4880"]


def test_ticket_plan_keeps_ancestor_keys_for_nested_area_selection():
    units = [
        {
            "key": "area-c1",
            "name": "C1區",
            "price": 3880,
            "available": True,
            "max_quantity": 4,
            "general_admission": False,
            "selection_mode": "area_panel",
            "ancestor_keys": ["price-3880"],
        }
    ]

    plan = build_ticket_plan(make_event(area_keywords=["C"]), units)

    assert plan["ancestor_keys"] == ["price-3880"]


def test_page_state_precedence():
    assert detect_page_state({"login_required": True, "queue": True}) == "login_required"
    assert detect_page_state({"queue": True}) == "queue"
    assert detect_page_state({"payment_boundary": True}) == "payment_boundary"


def test_area_keywords_override_legacy_keyword():
    assert resolve_area_keywords(make_event(area_keyword="VIP")) == ["S區", "一般區"]
    assert resolve_area_keywords(make_event(area_keywords=[], area_keyword="VIP")) == ["VIP"]


def test_select_session_matches_date():
    sessions = [
        {"key": "early", "date": "2026-11-21", "available": True},
        {"key": "target", "date": "2026-11-22(日)", "available": True},
    ]
    assert select_session(make_event(), sessions)["key"] == "target"


def test_select_session_normalizes_date_separators():
    sessions = [
        {"key": "target", "date": "2026/11/22（日）", "available": True},
    ]
    assert select_session(make_event(date_keyword="2026-11-22"), sessions)["key"] == "target"


def test_ticket_plan_falls_back_without_breaking_price_limit():
    units = [
        {"key": "s", "name": "S區", "price": 4000, "available": True, "max_quantity": 1, "general_admission": True},
        {"key": "general", "name": "一般區", "price": 2500, "available": True, "max_quantity": 4, "general_admission": True},
    ]
    plan = build_ticket_plan(make_event(), units)
    assert plan == {
        "ticket_key": "general",
        "ticket_name": "一般區",
        "price": 2500,
        "quantity": 2,
        "general_admission": True,
        "selection_mode": "quantity",
    }


def test_adjacent_seats_fall_back_to_next_area():
    seats = [
        {"key": "a-8", "area": "A區", "row": "3", "seat_number": "8", "price": 3200, "available": True, "selectable": True},
        {"key": "a-10", "area": "A區", "row": "3", "seat_number": "10", "price": 3200, "available": True, "selectable": True},
        {"key": "b-1", "area": "B區", "row": "1", "seat_number": "1", "price": 2500, "available": True, "selectable": True},
        {"key": "b-2", "area": "B區", "row": "1", "seat_number": "2", "price": 2500, "available": True, "selectable": True},
    ]
    event = make_event(area_keywords=["A區", "B區"], max_price=3200)
    assert [seat["key"] for seat in select_adjacent_seats(event, seats)] == ["b-1", "b-2"]


def test_order_review_rejects_wrong_total():
    event = make_event()
    plan = {"ticket_name": "一般區", "price": 2500, "quantity": 2}
    errors = validate_order_review(
        event,
        plan,
        {"ticket_name": "一般區", "quantity": 2, "subtotal": 6000, "fees": 30, "total": 6030},
    )
    assert errors == ["票款小計 6000 與預期票款 5000 不符"]


def test_order_review_handles_missing_numeric_values():
    errors = validate_order_review(
        make_event(),
        {"ticket_name": "一般區", "price": 2500, "quantity": 2},
        {"ticket_name": "一般區", "quantity": None, "subtotal": None, "fees": None, "total": None},
    )
    assert "訂單張數與選擇張數不符" in errors
    assert "票款小計 None 與預期票款 5000 不符" in errors
    assert "訂單總額與票款小計加附加費用不符" in errors
