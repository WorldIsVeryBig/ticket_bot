# Ticket Plus 遠大售票平台實作計畫

> **給執行此計畫的代理：** 必須使用 `superpowers:subagent-driven-development`（建議）或 `superpowers:executing-plans`，逐項執行本計畫。每個步驟都使用核取方塊（`- [ ]`）追蹤狀態。

**目標：** 新增以瀏覽器操作為主的 Ticket Plus 遠大售票平台，按照設定選擇場次、候選票種、張數，必要時選擇相鄰座位，最後停在付款操作之前。

**架構：** 新增純邏輯的 `ticketplus_parser` 模組，處理網址、頁面狀態、票種、座位與訂單計畫；另新增使用既有 `BrowserEngine`／`PageWrapper` 抽象介面的 `TicketPlusBot` 瀏覽器狀態機。擴充現有活動 YAML 與 CLI 分派，同時不改變 Tixcraft、KKTIX 的既有行為。

**技術：** Python 3.11 以上、dataclasses、標準函式庫 `re`／`html`／`urllib.parse`、Click、pytest、pytest-asyncio，以及現有 nodriver／Playwright 抽象介面。

**設計規格：** `docs/superpowers/specs/2026-09-18-ticketplus-platform-design.md`

## 全域限制

- 沿用現有 `config.yaml`；所有新增活動欄位都是選填，舊版 YAML 必須可以原樣載入。
- Ticket Plus 第一版只使用瀏覽器流程，絕不交給 Tixcraft 的 `api_mode`。
- 沿用 Chrome profile 登入狀態；不得新增密碼、OTP、Cookie、信用卡、銀行或付款資料欄位。
- 不自動解答或繞過 CAPTCHA，也不繞過排隊與流量管制。
- 絕不點擊付款、銀行授權、信用卡、ATM 或 3D Secure 的送出控制項。
- 只有訂單確認摘要符合所選票種、張數及價格上限時，才視為成功。
- 診斷紀錄不得寫入 Cookie、Authorization header、會員資料或付款資料。
- 除非失敗測試證明現有介面無法實作已核准設計，否則不得修改 `BrowserEngine` 或 `PageWrapper`。
- 上傳的原始碼快照沒有 `.git`；計畫中的 commit 指令只供你在原始 Git 專案建立檢查點，本工作區不會執行。

## 檔案分工

- `src/ticket_bot/config.py`：加入 Ticket Plus 選填偏好，同時維持舊版活動 YAML 相容。
- `src/ticket_bot/platforms/ticketplus_parser.py`：純資料正規化、頁面狀態、候選票種、相鄰座位與訂單核對邏輯。
- `src/ticket_bot/platforms/ticketplus.py`：瀏覽器生命週期、DOM 資料擷取、狀態轉移、`run` 與 `watch`。
- `src/ticket_bot/cli.py`：平台工廠、登入網址、run/watch 平台白名單及 API 模式隔離。
- `tests/fixtures/ticketplus/*.json`：已移除敏感資料的頁面 metadata，涵蓋公開活動、登入、排隊、站席票、劃位座位及訂單確認。
- `tests/test_ticketplus_parser.py`：純解析與選擇邏輯測試。
- `tests/test_ticketplus_flow.py`：模擬瀏覽器狀態機測試。
- `tests/test_config.py`：新舊 YAML 相容性測試。
- `tests/test_cli_ticketplus.py`：CLI 平台工廠與指令分派測試。
- `config.yaml.example`：以註解形式提供預設不啟用的 Ticket Plus 範例。
- `README.md`：登入、執行、監控、候選降級與付款停止邊界說明。

---

### 任務 1：向後相容的活動設定

**檔案：**
- 修改：`src/ticket_bot/config.py:16-24`
- 修改：`tests/test_config.py:13-181`

**介面：**
- 使用：`load_config()` 現有的 `EventConfig(**event_yaml)` 建構方式。
- 產出：供解析器與 Bot 使用的 `EventConfig.area_keywords: list[str]` 及 `EventConfig.max_price: int | None`。

- [ ] **步驟 1：先撰寫新舊 YAML 的失敗測試**

在 `tests/test_config.py` 加入：

```python
def test_ticketplus_event_preferences_load(tmp_path):
    (tmp_path / "config.yaml").write_text(
        """
events:
  - name: Ticket Plus
    platform: ticketplus
    url: https://ticketplus.com.tw/activity/6d2868aeec40f3676950edc8fb70a1f4
    ticket_count: 2
    date_keyword: "2026-11-22"
    area_keyword: ""
    area_keywords: ["S區", "一般區"]
    max_price: 4000
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / ".env").write_text("", encoding="utf-8")

    event = load_config(
        str(tmp_path / "config.yaml"), str(tmp_path / ".env")
    ).events[0]

    assert event.area_keywords == ["S區", "一般區"]
    assert event.max_price == 4000


def test_old_event_yaml_gets_ticketplus_defaults(config_dir):
    event = load_config(
        str(config_dir / "config.yaml"), str(config_dir / ".env")
    ).events[0]

    assert event.area_keywords == []
    assert event.max_price is None
```

- [ ] **步驟 2：執行測試並確認為 RED（預期失敗）**

執行：

```bash
python -m pytest tests/test_config.py::test_ticketplus_event_preferences_load tests/test_config.py::test_old_event_yaml_gets_ticketplus_defaults -q
```

預期：FAIL，因為 `EventConfig` 尚不接受 `area_keywords`，也還沒有這兩個屬性。

- [ ] **步驟 3：加入選填 dataclass 欄位**

將 `EventConfig` 改為：

```python
@dataclass
class EventConfig:
    name: str
    platform: str
    url: str
    ticket_count: int = 2
    date_keyword: str = ""
    area_keyword: str = ""
    area_keywords: list[str] = field(default_factory=list)
    max_price: int | None = None
    sale_time: str = ""
    presale_code: str = ""
```

- [ ] **步驟 4：執行設定測試並確認為 GREEN（通過）**

執行：

```bash
python -m pytest tests/test_config.py -q
```

預期：全部設定測試 PASS。

- [ ] **步驟 5：在原始 Git 專案建立 commit 檢查點**

```bash
git add src/ticket_bot/config.py tests/test_config.py
git commit -m "feat: add Ticket Plus event preferences"
```

---

### 任務 2：Ticket Plus 純選擇與驗證邏輯

**檔案：**
- 建立：`src/ticket_bot/platforms/ticketplus_parser.py`
- 建立：`tests/test_ticketplus_parser.py`
- 建立：`tests/fixtures/ticketplus/activity.json`
- 建立：`tests/fixtures/ticketplus/login_required.json`
- 建立：`tests/fixtures/ticketplus/queue.json`
- 建立：`tests/fixtures/ticketplus/standing_tickets.json`
- 建立：`tests/fixtures/ticketplus/assigned_seats.json`
- 建立：`tests/fixtures/ticketplus/order_review.json`

**介面：**
- 使用：任務 1 的 `EventConfig`，以及 `TicketPlusBot` 擷取並移除敏感資料後的 metadata 字典。
- 產出：
  - `extract_activity_id(url: str) -> str`
  - `normalize_text(value: str) -> str`
  - `parse_price(value: str) -> int | None`
  - `resolve_area_keywords(event: EventConfig) -> list[str]`
  - `detect_page_state(snapshot: dict) -> str`
  - `select_session(event: EventConfig, sessions: list[dict]) -> dict`
  - `build_ticket_plan(event: EventConfig, ticket_units: list[dict]) -> dict`
  - `select_adjacent_seats(event: EventConfig, seats: list[dict]) -> list[dict]`
  - `validate_order_review(event: EventConfig, plan: dict, review: dict) -> list[str]`

- [ ] **步驟 1：建立不含敏感資料的 JSON fixture**

使用下列固定資料結構：

```json
{
  "page_url": "https://ticketplus.com.tw/activity/6d2868aeec40f3676950edc8fb70a1f4",
  "title": "白玖ウタノ UTANOLIVE!! In Taipei 2026",
  "login_required": false,
  "queue": false,
  "sessions": [
    {"key": "session-1", "name": "白玖ウタノ UTANOLIVE!! In Taipei 2026", "date": "2026-11-22(日)", "time": "17:00 ~ 19:00", "status": "立即購買", "available": true}
  ]
}
```

```json
{
  "ticket_units": [
    {"key": "s-zone", "name": "S區", "price_text": "NT$4,000", "price": 4000, "status": "可售", "available": true, "max_quantity": 4, "general_admission": true},
    {"key": "general", "name": "一般區", "price_text": "NT$2,500", "price": 2500, "status": "可售", "available": true, "max_quantity": 4, "general_admission": true}
  ]
}
```

```json
{
  "seats": [
    {"key": "a-8", "area": "A區", "row": "3", "seat_number": "8", "price": 3200, "available": true, "selectable": true},
    {"key": "a-9", "area": "A區", "row": "3", "seat_number": "9", "price": 3200, "available": true, "selectable": true},
    {"key": "a-11", "area": "A區", "row": "3", "seat_number": "11", "price": 3200, "available": true, "selectable": true},
    {"key": "b-1", "area": "B區", "row": "1", "seat_number": "1", "price": 2500, "available": true, "selectable": true},
    {"key": "b-2", "area": "B區", "row": "1", "seat_number": "2", "price": 2500, "available": true, "selectable": true}
  ]
}
```

- [ ] **步驟 2：先撰寫網址、價格、狀態及候選順序的失敗測試**

建立 `tests/test_ticketplus_parser.py`，內容包含：

```python
from ticket_bot.config import EventConfig
from ticket_bot.platforms.ticketplus_parser import (
    build_ticket_plan,
    detect_page_state,
    extract_activity_id,
    parse_price,
    resolve_area_keywords,
    select_adjacent_seats,
    select_session,
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


def test_page_state_precedence():
    assert detect_page_state({"login_required": True, "queue": True}) == "login_required"
    assert detect_page_state({"queue": True}) == "queue"
    assert detect_page_state({"payment_boundary": True}) == "payment_boundary"


def test_area_keywords_override_legacy_keyword():
    assert resolve_area_keywords(make_event(area_keyword="VIP")) == ["S區", "一般區"]
    assert resolve_area_keywords(make_event(area_keywords=[], area_keyword="VIP")) == ["VIP"]
```

- [ ] **步驟 3：執行聚焦測試並確認為 RED**

執行：

```bash
python -m pytest tests/test_ticketplus_parser.py -q
```

預期：測試收集階段 FAIL，因為 `ticketplus_parser` 尚未建立。

- [ ] **步驟 4：實作文字正規化與頁面狀態辨識**

建立 `ticketplus_parser.py`，加入常數與最小可用函式：

```python
from __future__ import annotations

import re
from urllib.parse import urlparse

from ticket_bot.config import EventConfig

PAGE_STATES = {
    "login_required", "sale_not_started", "session_select", "queue",
    "ticket_select", "general_admission", "seat_map", "order_review",
    "payment_boundary", "unknown",
}


def normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def extract_activity_id(url: str) -> str:
    parsed = urlparse(url)
    if parsed.hostname not in {"ticketplus.com.tw", "www.ticketplus.com.tw"}:
        return ""
    match = re.fullmatch(r"/activity/([A-Za-z0-9]+?)/?", parsed.path)
    return match.group(1) if match else ""


def parse_price(value: str) -> int | None:
    text = normalize_text(value)
    if "免費" in text:
        return 0
    matches = re.findall(r"(?:NT\$?|TWD\$?|\$)\s*([0-9][0-9,]*)", text, re.I)
    return int(matches[-1].replace(",", "")) if matches else None


def resolve_area_keywords(event: EventConfig) -> list[str]:
    ordered = event.area_keywords or ([event.area_keyword] if event.area_keyword else [])
    return [normalize_text(value) for value in ordered if normalize_text(value)]


def detect_page_state(snapshot: dict) -> str:
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
        if snapshot.get(key):
            return state
    return "unknown"
```

- [ ] **步驟 5：再次執行聚焦測試，確認第一組測試為 GREEN**

執行：

```bash
python -m pytest tests/test_ticketplus_parser.py -q
```

預期：網址、價格、狀態與關鍵字測試 PASS；選票函式會在下一步補上。

- [ ] **步驟 6：先撰寫選票、相鄰座位與訂單核對的失敗測試**

加入以下測試斷言：

```python
def test_select_session_matches_date():
    sessions = [
        {"key": "early", "date": "2026-11-21", "available": True},
        {"key": "target", "date": "2026-11-22(日)", "available": True},
    ]
    assert select_session(make_event(), sessions)["key"] == "target"


def test_ticket_plan_falls_back_without_breaking_price_limit():
    units = [
        {"key": "s", "name": "S區", "price": 4000, "available": True, "max_quantity": 1, "general_admission": True},
        {"key": "general", "name": "一般區", "price": 2500, "available": True, "max_quantity": 4, "general_admission": True},
    ]
    plan = build_ticket_plan(make_event(), units)
    assert plan == {"ticket_key": "general", "ticket_name": "一般區", "price": 2500, "quantity": 2, "general_admission": True}


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
```

- [ ] **步驟 7：執行新增測試並確認為 RED**

執行：

```bash
python -m pytest tests/test_ticketplus_parser.py -q
```

預期：FAIL，因為四個選擇／驗證函式尚未實作。

- [ ] **步驟 8：實作最小可用的選擇與驗證函式**

實作要求：

```python
def select_session(event: EventConfig, sessions: list[dict]) -> dict:
    available = [item for item in sessions if item.get("available")]
    if event.date_keyword:
        available = [item for item in available if event.date_keyword.casefold() in normalize_text(item.get("date", "")).casefold()]
    if not available:
        raise ValueError(f"Ticket Plus 找不到符合日期的可售場次: {event.date_keyword or '第一個可售'}")
    return available[0]


def build_ticket_plan(event: EventConfig, ticket_units: list[dict]) -> dict:
    keywords = resolve_area_keywords(event)
    ordered_keywords = keywords or [""]
    for keyword in ordered_keywords:
        for unit in ticket_units:
            name = normalize_text(unit.get("name", ""))
            price = unit.get("price")
            if keyword and keyword.casefold() not in name.casefold():
                continue
            if not unit.get("available") or int(unit.get("max_quantity", 0)) < event.ticket_count:
                continue
            if event.max_price is not None and (price is None or price > event.max_price):
                continue
            return {
                "ticket_key": unit["key"], "ticket_name": name,
                "price": price, "quantity": event.ticket_count,
                "general_admission": bool(unit.get("general_admission")),
            }
    raise ValueError("Ticket Plus 找不到符合候選順序、票數與價格上限的票種")
```

相鄰座位必須使用下列分組與連續窗口規則：

```python
def select_adjacent_seats(event: EventConfig, seats: list[dict]) -> list[dict]:
    keywords = resolve_area_keywords(event) or [""]
    for keyword in keywords:
        groups: dict[tuple[str, str], list[tuple[int, dict]]] = {}
        for seat in seats:
            area = normalize_text(seat.get("area", ""))
            if keyword and keyword.casefold() not in area.casefold():
                continue
            if not seat.get("available") or not seat.get("selectable"):
                continue
            price = seat.get("price")
            if event.max_price is not None and (price is None or price > event.max_price):
                continue
            number_text = normalize_text(str(seat.get("seat_number", "")))
            if not number_text.isdigit():
                continue
            group_key = (area, normalize_text(seat.get("row", "")))
            groups.setdefault(group_key, []).append((int(number_text), seat))
        for group in groups.values():
            ordered = sorted(group, key=lambda item: item[0])
            for start in range(0, len(ordered) - event.ticket_count + 1):
                window = ordered[start : start + event.ticket_count]
                if all(window[index + 1][0] == window[index][0] + 1 for index in range(len(window) - 1)):
                    return [item[1] for item in window]
    raise ValueError("Ticket Plus 找不到足夠的同排相鄰座位")
```

票款小計與頁面列出的附加費用必須分開驗證：

```python
def validate_order_review(event: EventConfig, plan: dict, review: dict) -> list[str]:
    errors: list[str] = []
    expected_subtotal = int(plan["price"]) * int(plan["quantity"])
    if normalize_text(review.get("ticket_name", "")) != normalize_text(plan["ticket_name"]):
        errors.append("訂單票種與選擇票種不符")
    if int(review.get("quantity", 0)) != int(plan["quantity"]):
        errors.append("訂單張數與選擇張數不符")
    if int(review.get("subtotal", -1)) != expected_subtotal:
        errors.append(f"票款小計 {review.get('subtotal')} 與預期票款 {expected_subtotal} 不符")
    fees = int(review.get("fees", 0))
    if int(review.get("total", -1)) != int(review.get("subtotal", -1)) + fees:
        errors.append("訂單總額與票款小計加附加費用不符")
    if event.max_price is not None and int(plan["price"]) > event.max_price:
        errors.append("單張票價超過 max_price")
    return errors
```

- [ ] **步驟 9：執行全部解析器測試並確認為 GREEN**

執行：

```bash
python -m pytest tests/test_ticketplus_parser.py -q
```

預期：全部解析器測試 PASS。

- [ ] **步驟 10：在原始 Git 專案建立 commit 檢查點**

```bash
git add src/ticket_bot/platforms/ticketplus_parser.py tests/test_ticketplus_parser.py tests/fixtures/ticketplus
git commit -m "feat: add Ticket Plus selection parser"
```

---

### 任務 3：以瀏覽器為主的 Ticket Plus 狀態機

**檔案：**
- 建立：`src/ticket_bot/platforms/ticketplus.py`
- 建立：`tests/test_ticketplus_flow.py`

**介面：**
- 使用：任務 2 的全部解析器函式，以及既有的 `BrowserEngine`、`PageWrapper`、`AppConfig`、`EventConfig`、`SessionConfig`。
- 產出：`TicketPlusBot(config, event, session=None)`，提供 `start_browser()`、`open_login_page()`、`run()`、`watch(interval=5.0)` 及 `close()`。

- [ ] **步驟 1：建立會記錄操作的模擬瀏覽器**

在 `tests/test_ticketplus_flow.py` 建立 `FakeTicketPlusPage`，讓頁面快照依序經過：

```python
ACTIVITY = {"sessions": [{"key": "session-1", "date": "2026-11-22(日)", "available": True}]}
TICKETS = {"ticket_units": [{"key": "general", "name": "一般區", "price": 2500, "available": True, "max_quantity": 4, "general_admission": True}]}
REVIEW = {
    "order_review": True, "ticket_name": "一般區", "quantity": 2,
    "subtotal": 5000, "fees": 30, "total": 5030,
    "payment_boundary": True,
}
```

其 `evaluate()` 遇到包含 `ticketPlusSnapshot` 的表達式時回傳目前快照；只有遇到 `ticketPlusSelectSession` 或 `ticketPlusSelectTickets` 才能進到下一個快照，並將動作名稱加入 `self.actions`。任何包含 `payment`、`credit`、`atm` 或 `3d` 的表達式都必須拋出 `AssertionError`。

- [ ] **步驟 2：先撰寫流程失敗測試**

```python
@pytest.mark.asyncio
async def test_run_stops_at_payment_boundary(monkeypatch):
    page = FakeTicketPlusPage([ACTIVITY, TICKETS, REVIEW])
    engine = FakeEngine(page)
    monkeypatch.setattr(ticketplus_module, "create_engine", lambda _: engine)
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
    sold_out = {"ticket_units": [{"key": "general", "name": "一般區", "price": 2500, "available": False, "max_quantity": 0}]}
    page = FakeTicketPlusPage([sold_out, TICKETS, REVIEW])
    monkeypatch.setattr(ticketplus_module, "create_engine", lambda _: FakeEngine(page))
    bot = TicketPlusBot(make_config(), make_event())

    assert await bot.watch(interval=0) is True
    assert "select_ticket:general:2" in page.actions
```

- [ ] **步驟 3：執行流程測試並確認為 RED**

執行：

```bash
python -m pytest tests/test_ticketplus_flow.py -q
```

預期：測試收集階段 FAIL，因為 `ticketplus.py` 尚未建立。

- [ ] **步驟 4：實作 `TicketPlusBot` 瀏覽器生命週期與頁面快照擷取**

使用下列常數與明確的方法合約：

```python
TICKETPLUS_HOME_URL = "https://ticketplus.com.tw/"
MAX_STATE_STEPS = 80
STATE_POLL_SECONDS = 0.25


class TicketPlusBot:
    """Browser-first Ticket Plus adapter that stops before payment."""

    def __init__(self, config: AppConfig, event: EventConfig, session: SessionConfig | None = None):
        self.config = config
        self.event = event
        self.session = session
        self.engine: BrowserEngine = create_engine(config.browser.engine)
        self.page: PageWrapper | None = None
        self.last_success_info = ""
        self._ticket_plan: dict | None = None

    # start_browser launches the configured engine with the session profile.
    # _ensure_page launches once and creates one blank page.
    # open_login_page navigates that page to TICKETPLUS_HOME_URL.
    # _snapshot returns only the approved sanitized metadata keys below.
    # run executes at most MAX_STATE_STEPS and returns True only at a validated payment boundary.
    # watch retries unavailable/sold-out states at the requested interval and returns after success.
    # close delegates to self.engine.close().
```

`_snapshot()` 只執行一段唯讀的 `ticketPlusSnapshot` JavaScript，且只能回傳：

```text
page_url, title, login_required, sale_not_started, queue,
sessions, ticket_units, seat_map, seats, order_review,
ticket_name, quantity, subtotal, fees, total,
payment_boundary, verification_required
```

不得回傳表單輸入值、Cookie、local/session storage、授權 header、會員欄位或付款欄位。

- [ ] **步驟 5：實作安全的 DOM 操作與狀態轉移**

不同操作使用不同 JavaScript 標記，讓模擬測試可以分辨：

```python
async def _select_session(self, key: str) -> None:
    result = await self.page.evaluate(
        f"window.ticketPlusSelectSession && window.ticketPlusSelectSession({json.dumps(key)})"
    )
    if result is not True:
        raise RuntimeError(f"Ticket Plus 無法選擇場次: {key}")


async def _select_ticket(self, plan: dict) -> None:
    payload = json.dumps(plan, ensure_ascii=False)
    result = await self.page.evaluate(
        f"window.ticketPlusSelectTickets && window.ticketPlusSelectTickets({payload})"
    )
    if result is not True:
        raise RuntimeError(f"Ticket Plus 無法選擇票種: {plan['ticket_name']}")
```

正式執行時，每個頁面只注入一次固定內容的具名輔助函式；尋找元素時優先使用穩定屬性（`data-*`、input/select value、label），最後才使用正規化後的可見文字。不得直接將未跳脫的 YAML 文字組成 selector。

`run()` 迴圈必須：

1. 開啟 `event.url` 並輪詢 `_snapshot()`。
2. 遇到 `login_required` 時清楚回報失敗原因。
3. 遇到 `queue` 或 `verification_required` 時停留在原頁等待，不重整也不繞過。
4. 使用 `select_session()` 選擇場次。
5. 使用 `build_ticket_plan()` 建立並套用選票計畫。
6. 遇到 `seat_map` 時呼叫 `select_adjacent_seats()`，且只選取該函式回傳的座位 key。
7. 進入 `order_review`／`payment_boundary` 時呼叫 `validate_order_review()`。
8. 只有核對錯誤為空時才設定 `last_success_info` 並回傳 `True`，之後不得再點擊。
9. 頁面狀態無效、摘要不符、等待逾時或遇到不支援的 Canvas 座位圖時，以 `logger.exception` 記錄並回傳 `False`。

- [ ] **步驟 6：執行流程測試並確認為 GREEN**

執行：

```bash
python -m pytest tests/test_ticketplus_flow.py -q
```

預期：全部流程測試 PASS，且模擬瀏覽器證明程式沒有嘗試任何付款操作。

- [ ] **步驟 7：加入明確的座位圖與人工驗證測試**

加入測試，確認：

- DOM／SVG 座位圖只會選取 `select_adjacent_seats()` 回傳的 key；
- `seat_map=True` 但沒有可讀座位資料時，不執行操作並回傳 `False`；
- 人工驗證快照只會呼叫 `sleep()` 等待，直到進入下一個安全狀態；
- 排隊快照不會被程式重整，並可在網站放行後繼續；
- 未知頁面回傳 `False` 且不執行操作。

- [ ] **步驟 8：執行全部 Ticket Plus 流程測試**

執行：

```bash
python -m pytest tests/test_ticketplus_flow.py -q
```

預期：全部 Ticket Plus 流程測試 PASS。

- [ ] **步驟 9：在原始 Git 專案建立 commit 檢查點**

```bash
git add src/ticket_bot/platforms/ticketplus.py tests/test_ticketplus_flow.py
git commit -m "feat: add Ticket Plus browser flow"
```

---

### 任務 4：CLI 分派、登入、執行與釋票監控

**檔案：**
- 修改：`src/ticket_bot/cli.py:15-28,300-332,335-430`
- 建立：`tests/test_cli_ticketplus.py`

**介面：**
- 使用：任務 3 的 `TicketPlusBot`。
- 產出：`ticket-bot login --platform ticketplus`，並讓 `run`／`watch` 可以選取 Ticket Plus 活動。

- [ ] **步驟 1：先撰寫 CLI 失敗測試**

```python
from click.testing import CliRunner

from ticket_bot import cli as cli_module
from ticket_bot.config import AppConfig, BrowserConfig, EventConfig, SessionConfig


def test_factory_builds_ticketplus_bot(monkeypatch):
    marker = object()
    monkeypatch.setattr("ticket_bot.platforms.ticketplus.TicketPlusBot", lambda *args, **kwargs: marker)
    cfg = AppConfig(browser=BrowserConfig())
    event = EventConfig(name="TP", platform="ticketplus", url="https://ticketplus.com.tw/activity/abc")
    session = SessionConfig()
    assert cli_module._create_platform_bot(cfg, event, session) is marker


def test_login_help_lists_ticketplus():
    result = CliRunner().invoke(cli_module.cli, ["login", "--help"])
    assert result.exit_code == 0
    assert "ticketplus" in result.output
```

另外加入模擬 `run` 指令測試：載入一個 `platform: ticketplus` 活動，確認假 Bot 的 `run()` 有被呼叫；再加入對應的 `watch` 目標測試。即使傳入 `--api`，也必須確認建立的是 `TicketPlusBot`，而不是 `TixcraftApiBot`。

- [ ] **步驟 2：執行 CLI 測試並確認為 RED**

執行：

```bash
python -m pytest tests/test_cli_ticketplus.py -q
```

預期：FAIL，因為平台工廠與 Click 選項尚未包含 Ticket Plus。

- [ ] **步驟 3：加入明確的 Ticket Plus 分派**

在 Tixcraft API 分支之前加入：

```python
if ev.platform == "ticketplus":
    from ticket_bot.platforms.ticketplus import TicketPlusBot
    return TicketPlusBot(cfg, ev, session=session)
```

只有活動平台為 Tixcraft 時才允許使用 Tixcraft API：

```python
use_api = ev.platform == "tixcraft" and (api or cfg.browser.api_mode != "off")
```

- [ ] **步驟 4：加入登入與指令平台白名單支援**

使用：

```python
SUPPORTED_PURCHASE_PLATFORMS = {"tixcraft", "kktix", "ticketplus"}
LOGIN_URLS = {
    "tixcraft": "https://tixcraft.com/login",
    "kktix": "https://kktix.com/users/sign_in",
    "ticketplus": "https://ticketplus.com.tw/",
}
```

更新 Click 選項、登入網址查找、`run` 說明文字，以及所有 `run`／`watch` 目標篩選，統一使用 `SUPPORTED_PURCHASE_PLATFORMS`。

- [ ] **步驟 5：執行 CLI 測試並確認為 GREEN**

執行：

```bash
python -m pytest tests/test_cli_ticketplus.py tests/test_cli_watch.py -q
```

預期：全部 CLI 與 watch 規劃測試 PASS。

- [ ] **步驟 6：在原始 Git 專案建立 commit 檢查點**

```bash
git add src/ticket_bot/cli.py tests/test_cli_ticketplus.py
git commit -m "feat: wire Ticket Plus into CLI"
```

---

### 任務 5：使用說明與完整驗證

**檔案：**
- 修改：`config.yaml.example`
- 修改：`README.md`
- 驗證：任務 1～4 修改過的全部檔案

**介面：**
- 使用：最終設定格式與 CLI 介面。
- 產出：可以直接複製執行的設定說明，以及通過驗證、僅含原始碼的交付壓縮檔。

- [ ] **步驟 1：加入註解形式的 Ticket Plus 範例**

加入 `config.yaml.example`，但預設不要啟用第二個活動：

```yaml
# Ticket Plus 遠大售票範例：依序嘗試 S區、一般區，單張不超過 4000 元
# - name: "Ticket Plus"
#   platform: ticketplus
#   url: "https://ticketplus.com.tw/activity/6d2868aeec40f3676950edc8fb70a1f4"
#   ticket_count: 2
#   date_keyword: "2026-11-22"
#   area_keyword: ""
#   area_keywords:
#     - "S區"
#     - "一般區"
#   max_price: 4000
#   sale_time: ""
#   presale_code: ""
```

- [ ] **步驟 2：在 README 加入確切指令與自動化邊界**

記錄以下指令：

```bash
ticket-bot --config config.yaml login --platform ticketplus
ticket-bot --config config.yaml run --event "Ticket Plus"
ticket-bot --config config.yaml watch --event "Ticket Plus" --interval 5
```

明確說明 CAPTCHA／OTP 仍需人工處理、只使用 Canvas 且沒有可讀座位資料的座位圖會安全停止，以及 Bot 必定在付款前停止。

- [ ] **步驟 3：執行格式與語法檢查**

執行：

```bash
python -m py_compile \
  src/ticket_bot/config.py \
  src/ticket_bot/cli.py \
  src/ticket_bot/platforms/ticketplus.py \
  src/ticket_bot/platforms/ticketplus_parser.py
python -m ruff check src/ticket_bot tests
```

預期：沒有語法錯誤，也沒有 Ruff 錯誤。

- [ ] **步驟 4：執行 Ticket Plus 聚焦測試**

執行：

```bash
python -m pytest \
  tests/test_config.py \
  tests/test_ticketplus_parser.py \
  tests/test_ticketplus_flow.py \
  tests/test_cli_ticketplus.py -q
```

預期：全部聚焦測試 PASS。

- [ ] **步驟 5：執行完整回歸測試**

執行：

```bash
python -m pytest -q
```

預期：既有 Tixcraft、KKTIX、瀏覽器、驗證碼、通知、Proxy、RL，以及新增 Ticket Plus 測試全部 PASS。

- [ ] **步驟 6：確認交付內容不含敏感資料與大型目錄**

執行：

```bash
find . -maxdepth 2 -type d \( \
  -name '.git' -o -name '.env' -o -name 'chrome_profile*' -o \
  -name 'venv*' -o -name '.venv' -o -name 'captcha_training_data' -o \
  -name 'model' -o -name '__pycache__' -o -name '.pytest_cache' \
\) -print
```

將結果作為最終壓縮檔的排除清單。壓縮檔只保留原始碼、測試、文件、範例、`pyproject.toml`、README 與授權檔案。

- [ ] **步驟 7：在原始 Git 專案建立 commit 檢查點**

```bash
git add README.md config.yaml.example docs/superpowers
git commit -m "docs: document Ticket Plus workflow"
```

- [ ] **步驟 8：建立僅含原始碼的交付壓縮檔**

從專案上一層目錄將專案複製到暫存目錄，同時排除 `.git`、`.env`、Chrome profile、虛擬環境、log、訓練圖片、模型、cache、除錯 HTML、備份檔與使用者正在使用的 `config.yaml`；再將清理過的目錄壓縮為 `ticket-bot-public-ticketplus-source.zip`。

壓縮檔預期包含：

```text
ticket-bot-public/src/
ticket-bot-public/tests/
ticket-bot-public/docs/
ticket-bot-public/config.yaml.example
ticket-bot-public/README.md
ticket-bot-public/pyproject.toml
ticket-bot-public/LICENSE
```
