import pytest

from ticket_bot.config import AppConfig, BrowserConfig, EventConfig, KKTIXAutofillConfig
from ticket_bot.platforms import kktix as kktix_module
from ticket_bot.platforms.kktix import (
    KKTIXBot,
    _registration_dom_ready,
    build_registration_selection_plan,
)
from ticket_bot.platforms.kktix_parser import detect_kktix_challenge, parse_registration_page


KKTIX_REGISTRATION_HTML = """
<!doctype html>
<html lang="en-us">
  <head>
    <title>Test Event Reserve Tickets</title>
  </head>
  <body class="registration-horizontal">
    <div class="alert-wrapper align-center alert-info">
      <div class="alert">
        <span>Successfully logged in. Only KKTIX members who have completed mobile verification can purchase.</span>
      </div>
    </div>
    <div id="registrationsNewApp" class="main-wrapper clearfix">
      <table class="table table-striped"><tbody>
        <tr><th>Payment Terms</th><td>Credit Card</td></tr>
      </tbody></table>
      <div class="arena-ticket-wrapper" ng-controller="TicketsListCtrl">
        <div class="ticket-list">
          <div class="ticket-unit">
            <div class="display-table" id="ticket_1004800">
              <span class="ticket-name">全票<div class="small text-muted">A1, A2, A3</div></span>
              <span class="ticket-price"><span>TWD$5,880</span></span>
              <span class="ticket-quantity">
                <button class="btn-default minus" ng-click="quantityBtnClick(-1)" disabled="disabled"></button>
                <input type="text" value="0">
                <button class="btn-default plus" ng-click="quantityBtnClick(1)"></button>
              </span>
            </div>
          </div>
          <div class="ticket-unit">
            <div class="display-table" id="ticket_1004803">
              <span class="ticket-name">身心障礙票<div class="small text-muted">身障席</div></span>
              <span class="ticket-price"><span>TWD$2,940</span></span>
              <span class="ticket-quantity">Sold Out</span>
            </div>
          </div>
        </div>
        <div class="control-group">
          <label for="person_agree_terms" class="checkbox-inline">
            <input id="person_agree_terms" type="checkbox" value="agree">
          </label>
        </div>
        <div class="spinner-holder">
          <button ng-click="challenge()" disabled="disabled"><span>Pick Your Seat(s)</span></button>
          <button ng-click="challenge(1)" disabled="disabled"><span>Best Available</span></button>
        </div>
      </div>
    </div>
    <script>
      var TIXGLOBAL = {
        queueApi: { host: "queue.kktix.com", enable: true }
      };
    </script>
  </body>
</html>
"""


KKTIX_ORDER_HTML = """
<!doctype html>
<html lang="en-us">
  <head>
    <title>Test Event Reserve Tickets</title>
  </head>
  <body>
    <div ng-switch-when="countingDown" class="countdown-block">
      Your order has been reserved.
    </div>
    <div ng-if="isPending">
      <a href="javascript:void(0);" class="btn btn-default reselect-ticket">Cancel Ticket</a>
      <table class="table data-list cart-ticket-list">
        <tfoot>
          <tr class="highlight">
            <th class="ng-binding">Total Amount</th>
            <td colspan="2">TWD$5,880</td>
          </tr>
        </tfoot>
        <tbody>
          <tr>
            <td class="ticket-name">全票</td>
            <td class="ticket-data" colspan="2">
              <table class="cart-ticket-list-subtable with-seat">
                <tbody>
                  <tr>
                    <td ng-if="hasArena()" class="seat-info">
                      <ul><li>Area A1, Row 11, No. 69</li></ul>
                    </td>
                    <td class="align-right price-count">TWD$5,880 x 1</td>
                    <td class="align-right price-total">TWD$5,880</td>
                  </tr>
                </tbody>
              </table>
            </td>
          </tr>
        </tbody>
      </table>
      <div class="contact-info">
        <div class="control-group">
          <label class="control-label">姓名<abbr title="required">*</abbr></label>
          <div class="controls">
            <input type="text" name="contact[field_text_984583]">
          </div>
        </div>
        <div class="control-group">
          <label class="control-label">Email<abbr title="required">*</abbr></label>
          <div class="controls">
            <input type="text" name="contact[field_email_984584]">
          </div>
        </div>
      </div>
      <div class="attendee-info">
        <div class="control-group">
          <label class="control-label">姓名<abbr title="required">*</abbr></label>
          <div class="controls">
            <input type="text" name="attendees[0][field_text_984609]">
          </div>
        </div>
        <div class="control-group">
          <label class="control-label">身分證字號<abbr title="required">*</abbr></label>
          <div class="controls">
            <input type="text" name="attendees[0][field_idnumber_984612]">
          </div>
        </div>
        <div class="control-group">
          <label class="control-label">我理解並同意<abbr title="required">*</abbr></label>
          <div class="controls">
            <input type="radio" name="attendees[0][field_radio_984617]" value="471433">
          </div>
        </div>
      </div>
      <section class="additional-info">
        <label class="checkbox-inline"><input type="checkbox"> Show that you've been to this event on public page.</label>
      </section>
      <div class="form-actions plain align-center">
        <a class="btn btn-primary btn-lg" ng-click="confirmOrder()">Confirm Form</a>
      </div>
    </div>
  </body>
</html>
"""


class FakePage:
    def __init__(self):
        self._url = "about:blank"
        self._html = "<html></html>"

    async def goto(self, url: str) -> None:
        self._url = url
        if "/registrations/new" in url:
            self._html = KKTIX_REGISTRATION_HTML
        elif "/registrations/" in url:
            self._html = KKTIX_ORDER_HTML
        else:
            self._html = "<html><body>OK</body></html>"

    async def current_url(self) -> str:
        return self._url

    async def evaluate(self, expression: str):
        if expression == "document.documentElement.outerHTML":
            return self._html
        if "selected_quantity" in expression and "action_disabled_before_click" in expression:
            self._url = "https://kktix.com/events/476cd237/registrations/154208179-fb7b481d660dda4949a28a8bc86a54f6#/"
            self._html = KKTIX_ORDER_HTML
            return {
                "selected": True,
                "clicked_terms": True,
                "clicked_action": True,
                "action_disabled_before_click": False,
                "selected_quantity": 1,
                "errors": [],
            }
        if "missing_required" in expression and "contactFields" in expression:
            return {
                "applied": [
                    {"name": "contact[field_text_984583]", "label": "姓名", "value": "王小明"},
                    {"name": "contact[field_email_984584]", "label": "Email", "value": "demo@example.com"},
                ],
                "missing_required": [],
                "warnings": [],
            }
        if "window.confirm = () => true" in expression:
            self._url = "https://kktix.com/events/476cd237/registrations/new"
            self._html = KKTIX_REGISTRATION_HTML
            return True
        return None

    async def sleep(self, seconds: float) -> None:
        return None

    async def handle_cloudflare(self, timeout: float = 15.0) -> bool:
        return True


class FakeEngine:
    def __init__(self, page: FakePage):
        self.page = page

    async def launch(self, **kwargs) -> None:
        return None

    async def new_page(self, url: str = ""):
        if url:
            await self.page.goto(url)
        return self.page

    async def close(self) -> None:
        return None


def _make_config() -> AppConfig:
    return AppConfig(
        events=[],
        browser=BrowserConfig(engine="playwright", headless=True, executable_path="/usr/bin/chromium"),
        kktix=KKTIXAutofillConfig(
            enabled=True,
            contact_name="王小明",
            contact_email="demo@example.com",
            contact_phone="0912345678",
            attendee_id_numbers=["A123456789"],
        ),
    )


def test_detect_kktix_challenge_chinese_copy():
    assert detect_kktix_challenge("<html><body>正在執行安全驗證，請啟用 JavaScript 與 Cookie 以繼續</body></html>")


def test_build_registration_selection_plan_uses_area_keyword():
    info = parse_registration_page(
        KKTIX_REGISTRATION_HTML,
        "https://kktix.com/events/476cd237/registrations/new",
    )
    plan = build_registration_selection_plan(
        EventConfig(
            name="測試活動",
            platform="kktix",
            url="https://carrier.kktix.cc/events/476cd237",
            ticket_count=2,
            area_keyword="A1",
        ),
        info,
    )

    assert plan["ticket_id"] == "1004800"
    assert plan["quantity"] == 2
    assert plan["matched_by_keyword"] is True
    assert plan["action"] == "best_available"
    assert plan["action_selector"] == "button[ng-click='challenge(1)']"


def test_build_registration_selection_plan_rejects_missing_keyword():
    info = parse_registration_page(
        KKTIX_REGISTRATION_HTML,
        "https://kktix.com/events/476cd237/registrations/new",
    )

    with pytest.raises(ValueError, match="找不到符合票種關鍵字"):
        build_registration_selection_plan(
            EventConfig(
                name="測試活動",
                platform="kktix",
                url="https://carrier.kktix.cc/events/476cd237",
                area_keyword="VIP",
            ),
            info,
        )


def test_registration_dom_ready_waits_for_hydration_placeholders():
    assert _registration_dom_ready(
        {
            "challenge": False,
            "is_registration_page": True,
            "action_buttons": ["{{'new.not_skip_booking' | translate}}"],
            "ticket_units": [],
        }
    ) is False

    assert _registration_dom_ready(
        {
            "challenge": False,
            "is_registration_page": True,
            "action_buttons": ["Best Available"],
            "ticket_units": [
                {"status": "available", "selectable": False, "has_plus_button": False},
            ],
        }
    ) is False

    assert _registration_dom_ready(
        {
            "challenge": False,
            "is_registration_page": True,
            "action_buttons": ["Best Available"],
            "ticket_units": [
                {"status": "available", "selectable": True, "has_plus_button": True},
            ],
        }
    ) is True


@pytest.mark.asyncio
async def test_inspect_registration_page_uses_live_registration_url(monkeypatch):
    page = FakePage()
    engine = FakeEngine(page)
    monkeypatch.setattr(kktix_module, "create_engine", lambda _: engine)

    bot = KKTIXBot(
        _make_config(),
        EventConfig(
            name="測試活動",
            platform="kktix",
            url="https://carrier.kktix.cc/events/476cd237",
        ),
    )

    info = await bot.inspect_registration_page()

    assert info["page_url"] == "https://kktix.com/events/476cd237/registrations/new"
    assert info["event_slug"] == "476cd237"
    assert info["endpoints"]["queue_create_order"] == "https://queue.kktix.com/queue/476cd237"


@pytest.mark.asyncio
async def test_inspect_order_page_uses_current_url(monkeypatch):
    page = FakePage()
    engine = FakeEngine(page)
    monkeypatch.setattr(kktix_module, "create_engine", lambda _: engine)

    bot = KKTIXBot(
        _make_config(),
        EventConfig(
            name="測試活動",
            platform="kktix",
            url="https://carrier.kktix.cc/events/476cd237",
        ),
    )
    await bot._ensure_page()
    await page.goto("https://kktix.com/events/476cd237/registrations/154208179-fb7b481d660dda4949a28a8bc86a54f6#/")

    info = await bot.inspect_order_page()

    assert info["is_order_page"] is True
    assert info["endpoints"]["confirm_update_iframe"] == (
        "https://kktix.com/events/476cd237/registrations/154208179-fb7b481d660dda4949a28a8bc86a54f6?X-Requested-With=IFrame"
    )


@pytest.mark.asyncio
async def test_run_reserves_and_autofills_before_confirm(monkeypatch):
    page = FakePage()
    engine = FakeEngine(page)
    monkeypatch.setattr(kktix_module, "create_engine", lambda _: engine)

    bot = KKTIXBot(
        _make_config(),
        EventConfig(
            name="測試活動",
            platform="kktix",
            url="https://carrier.kktix.cc/events/476cd237",
            ticket_count=1,
        ),
    )

    success = await bot.run()

    assert success is True
    assert "/registrations/" in await page.current_url()
    assert "票種: 全票" in bot.last_success_info
    assert "總額: TWD$5,880" in bot.last_success_info


@pytest.mark.asyncio
async def test_cancel_order_returns_to_registration_page(monkeypatch):
    page = FakePage()
    engine = FakeEngine(page)
    monkeypatch.setattr(kktix_module, "create_engine", lambda _: engine)

    bot = KKTIXBot(
        _make_config(),
        EventConfig(
            name="測試活動",
            platform="kktix",
            url="https://carrier.kktix.cc/events/476cd237",
        ),
    )
    await bot._ensure_page()
    await page.goto("https://kktix.com/events/476cd237/registrations/154208179-fb7b481d660dda4949a28a8bc86a54f6#/")

    assert await bot.cancel_order() is True
    assert await page.current_url() == "https://kktix.com/events/476cd237/registrations/new"
