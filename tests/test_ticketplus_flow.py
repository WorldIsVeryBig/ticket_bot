"""Ticket Plus 瀏覽器狀態機測試。"""

import pytest

import ticket_bot.platforms.ticketplus as ticketplus_module
from ticket_bot.config import AppConfig, BrowserConfig, EventConfig
from ticket_bot.platforms.ticketplus import TicketPlusBot

ACTIVITY = {
    "sessions": [
        {"key": "session-1", "date": "2026-11-22(日)", "available": True}
    ]
}
TICKETS = {
    "ticket_units": [
        {
            "key": "general",
            "name": "一般區",
            "price": 2500,
            "available": True,
            "max_quantity": 4,
            "general_admission": True,
        }
    ]
}
REVIEW = {
    "order_review": True,
    "ticket_name": "一般區",
    "quantity": 2,
    "subtotal": 5000,
    "fees": 30,
    "total": 5030,
    "payment_boundary": True,
}


class FakeTicketPlusPage:
    def __init__(self, snapshots):
        self.snapshots = snapshots
        self.index = 0
        self.actions = []
        self.goto_calls = []
        self.sleep_calls = []

    async def goto(self, url):
        self.goto_calls.append(url)

    async def current_url(self):
        return self.goto_calls[-1] if self.goto_calls else "about:blank"

    async def evaluate(self, expression):
        lowered = expression.casefold()
        if "ticketplussnapshot" in lowered:
            snapshot = self.snapshots[min(self.index, len(self.snapshots) - 1)]
            if (
                (snapshot.get("queue") or snapshot.get("verification_required"))
                or (
                    snapshot.get("ticket_units")
                    and not any(unit.get("available") for unit in snapshot["ticket_units"])
                )
            ) and self.index < len(self.snapshots) - 1:
                self.index += 1
            return snapshot
        if any(word in lowered for word in ("payment", "credit", "atm", "3d")):
            raise AssertionError("測試禁止執行付款相關 JavaScript")
        if "ticketplusselectsession" in lowered:
            self.actions.append("select_session:session-1")
            self.index += 1
            return True
        if "ticketplusselecttickets" in lowered:
            self.actions.append("select_ticket:general:2")
            self.index += 1
            return True
        if "ticketplusselectseats" in lowered:
            self.actions.append("select_seats:b-1,b-2")
            self.index += 1
            return True
        return None

    async def sleep(self, seconds):
        self.sleep_calls.append(seconds)


class FakeEngine:
    def __init__(self, page):
        self.page = page

    async def launch(self, **kwargs):
        return None

    async def new_page(self, url=""):
        if url:
            await self.page.goto(url)
        return self.page

    async def close(self):
        return None


def make_config():
    return AppConfig(
        browser=BrowserConfig(engine="playwright", headless=True),
    )


def make_event(**overrides):
    values = {
        "name": "遠大測試活動",
        "platform": "ticketplus",
        "url": "https://ticketplus.com.tw/activity/6d2868aeec40f3676950edc8fb70a1f4",
        "ticket_count": 2,
        "date_keyword": "2026-11-22",
        "area_keywords": ["一般區"],
        "max_price": 4000,
    }
    values.update(overrides)
    return EventConfig(**values)


@pytest.mark.asyncio
async def test_run_stops_at_payment_boundary(monkeypatch):
    page = FakeTicketPlusPage([ACTIVITY, TICKETS, REVIEW])
    monkeypatch.setattr(ticketplus_module, "create_engine", lambda _: FakeEngine(page))
    bot = TicketPlusBot(make_config(), make_event())

    assert await bot.run() is True
    assert page.actions == ["select_session:session-1", "select_ticket:general:2"]
    assert "票種: 一般區" in bot.last_success_info
    assert "總額: 5030" in bot.last_success_info


@pytest.mark.asyncio
async def test_run_rejects_login_required(monkeypatch):
    page = FakeTicketPlusPage([{"login_required": True}])
    monkeypatch.setattr(ticketplus_module, "create_engine", lambda _: FakeEngine(page))
    bot = TicketPlusBot(make_config(), make_event())

    assert await bot.run() is False
    assert page.actions == []


@pytest.mark.asyncio
async def test_watch_retries_sold_out_then_succeeds(monkeypatch):
    sold_out = {
        "ticket_units": [
            {"key": "general", "name": "一般區", "price": 2500, "available": False, "max_quantity": 0}
        ]
    }
    page = FakeTicketPlusPage([sold_out, TICKETS, REVIEW])
    monkeypatch.setattr(ticketplus_module, "create_engine", lambda _: FakeEngine(page))
    bot = TicketPlusBot(make_config(), make_event())

    assert await bot.watch(interval=0) is True
    assert "select_ticket:general:2" in page.actions


@pytest.mark.asyncio
async def test_dom_seat_map_selects_only_adjacent_keys(monkeypatch):
    seat_map = {
        "seat_map": True,
        "seats": [
            {"key": "a-1", "area": "A區", "row": "1", "seat_number": "1", "price": 3000, "available": True, "selectable": True},
            {"key": "a-3", "area": "A區", "row": "1", "seat_number": "3", "price": 3000, "available": True, "selectable": True},
            {"key": "b-1", "area": "B區", "row": "1", "seat_number": "1", "price": 2500, "available": True, "selectable": True},
            {"key": "b-2", "area": "B區", "row": "1", "seat_number": "2", "price": 2500, "available": True, "selectable": True},
        ],
    }
    page = FakeTicketPlusPage([seat_map, REVIEW])
    monkeypatch.setattr(ticketplus_module, "create_engine", lambda _: FakeEngine(page))
    bot = TicketPlusBot(make_config(), make_event(area_keywords=["A區", "B區"]))
    bot._ticket_plan = {"ticket_name": "一般區", "price": 2500, "quantity": 2}

    assert await bot.run() is True
    assert page.actions == ["select_seats:b-1,b-2"]


@pytest.mark.asyncio
async def test_canvas_only_seat_map_stops_safely(monkeypatch):
    page = FakeTicketPlusPage([{"seat_map": True, "seats": []}])
    monkeypatch.setattr(ticketplus_module, "create_engine", lambda _: FakeEngine(page))
    bot = TicketPlusBot(make_config(), make_event())

    assert await bot.run() is False
    assert page.actions == []


@pytest.mark.asyncio
async def test_manual_verification_and_queue_only_wait(monkeypatch):
    page = FakeTicketPlusPage([
        {"verification_required": True},
        {"queue": True},
        ACTIVITY,
        TICKETS,
        REVIEW,
    ])
    monkeypatch.setattr(ticketplus_module, "create_engine", lambda _: FakeEngine(page))
    bot = TicketPlusBot(make_config(), make_event())

    assert await bot.run() is True
    assert len(page.goto_calls) == 1
    assert len(page.sleep_calls) >= 2


@pytest.mark.asyncio
async def test_unknown_page_stops_without_actions(monkeypatch):
    page = FakeTicketPlusPage([{}])
    monkeypatch.setattr(ticketplus_module, "create_engine", lambda _: FakeEngine(page))
    bot = TicketPlusBot(make_config(), make_event())

    assert await bot.run() is False
    assert page.actions == []
