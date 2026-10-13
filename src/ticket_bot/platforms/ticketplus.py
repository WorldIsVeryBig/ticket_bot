"""Ticket Plus（遠大售票）瀏覽器流程。

此模組只操作可見 DOM，建立訂單後停在付款邊界。登入、排隊與人工驗證
都交由使用者或網站原生流程處理，不讀取 Cookie、儲存空間或付款欄位。
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from urllib.parse import urlparse

from curl_cffi import requests as curl_requests

from ticket_bot.browser import BrowserEngine, PageWrapper, create_engine
from ticket_bot.config import AppConfig, EventConfig, SessionConfig
from ticket_bot.platforms.ticketplus_parser import (
    build_lab_ticket_plan,
    build_ticket_plan,
    detect_page_state,
    extract_activity_id,
    is_ticketplus_lab_url,
    parse_api_document,
    select_adjacent_seats,
    select_session,
    ticket_units_from_order_areas,
    validate_order_review,
)

logger = logging.getLogger(__name__)

TICKETPLUS_HOME_URL = "https://ticketplus.com.tw/"
MAX_STATE_STEPS = 240
MAX_QUEUE_STEPS = 2400
STATE_POLL_SECONDS = 0.25
LAB_QUEUE_TIMEOUT_SECONDS = 600
TICKETPLUS_SESSION_SELECTOR = (
    "[data-session-id], .session-item, .sesstion-item, .event-session, tr, .card"
)
TICKETPLUS_TICKET_SELECTOR = (
    "[data-ticket-id], [data-price], .ticket-unit, .ticket-item, .price-item, tr"
)


def is_payment_boundary(page_url: str, has_payment_action: bool) -> bool:
    """只有結帳路徑上的付款動作才算付款邊界。"""
    path = urlparse(page_url).path.casefold()
    is_checkout_path = bool(
        re.search(r"/(?:order|checkout|payment|cart|confirm)(?:/|$)", path)
    )
    return is_checkout_path and has_payment_action


_SNAPSHOT_JS = r"""
(function ticketPlusSnapshot() {
  const clean = value => String(value || '').replace(/\s+/g, ' ').trim();
  const body = clean(document.body && document.body.innerText);
  const visible = element => {
    if (!element) return false;
    const style = getComputedStyle(element);
    return style.display !== 'none' && style.visibility !== 'hidden';
  };
  const priceOf = text => {
    if (/免費/.test(text)) return 0;
    const hits = [...text.matchAll(/(?:NT(?:\$|\.)?|TWD\$?|\$)\s*([0-9][0-9,]*)/gi)];
    return hits.length ? Number(hits[hits.length - 1][1].replace(/,/g, '')) : null;
  };
  const unusable = text => /售完|已售完|完售|sold\s*out|不可購買|停止販售/i.test(text);
  const keyFor = (element, prefix, index) => {
    const key = clean(element.dataset.ticketBotKey || element.dataset.sessionId ||
      element.dataset.ticketId || element.dataset.id || element.id || `${prefix}-${index}`);
    element.dataset.ticketBotKey = key;
    return key;
  };

  const password = [...document.querySelectorAll('input[type="password"]')].some(visible);
  const loginDialog = [...document.querySelectorAll('[role="dialog"], .modal, .dialog')]
    .filter(visible).some(el => /登入|手機號碼|密碼/.test(clean(el.innerText)));
  const loggedIn = /[0-9]{3,4}\*{2,}[0-9]{3}[^]{0,80}登出/.test(body);
  const loginRequired = !loggedIn && (password || loginDialog);
  const queue = /流量管制|排隊(?:購票)?中|等候進入|queue|waiting room/i.test(body);
  const transientError = /您的操作好像出了一點問題|操作發生錯誤[^]{0,30}稍後再試/i.test(body);
  const verificationRequired = /人機驗證|安全驗證|驗證碼|captcha|turnstile/i.test(body);
  const saleNotStarted = /尚未開賣|開賣時間|即將開賣|coming soon/i.test(body);

  const sessions = [];
  [...document.querySelectorAll('__TICKETPLUS_SESSION_SELECTOR__')]
    .filter(visible).forEach((element, index) => {
      const text = clean(element.innerText);
      if (!text || text.length > 1200 || !(/立即購買|購票|尚未開賣|售完/.test(text))) return;
      const date = (text.match(/20\d{2}[\/.\-年]\s*\d{1,2}[\/.\-月]\s*\d{1,2}日?(?:\([^)]*\))?/) || [''])[0];
      sessions.push({
        key: keyFor(element, 'session', index), name: text, date,
        status: unusable(text) ? '售完' : (/尚未開賣/.test(text) ? '尚未開賣' : '立即購買'),
        available: !unusable(text) && !/尚未開賣/.test(text)
      });
    });

  const ticketUnits = [];
  const ticketCandidates = [...document.querySelectorAll('__TICKETPLUS_TICKET_SELECTOR__')];
  [...document.querySelectorAll('input[type="radio"], [role="radio"]')].forEach(control => {
    let candidate = control;
    for (let depth = 0; candidate && depth < 7; depth += 1, candidate = candidate.parentElement) {
      const text = clean(candidate.innerText);
      if (text && text.length <= 800 && priceOf(text) !== null) {
        ticketCandidates.push(candidate);
        break;
      }
    }
  });
  [...new Set(ticketCandidates)]
    .filter(visible).forEach((element, index) => {
      const text = clean(element.innerText);
      const price = priceOf(text);
      if (price === null || !text || text.length > 1600) return;
      const control = element.querySelector('select, input[type="number"], input[type="text"]');
      const radio = element.matches('input[type="radio"], [role="radio"]') ? element :
        element.querySelector('input[type="radio"], [role="radio"]');
      const plus = [...element.querySelectorAll('button, a')].find(el => /\+|增加|加/.test(clean(el.innerText) || el.getAttribute('aria-label') || ''));
      const remainingMatch = text.match(/剩餘\s*([0-9]+)/i);
      const radioDisabled = radio && (radio.disabled || radio.getAttribute('aria-disabled') === 'true' ||
        /disabled/.test(clean(radio.className && (radio.className.baseVal || radio.className))));
      let maxQuantity = 0;
      if (remainingMatch) {
        maxQuantity = Number(remainingMatch[1]);
      } else if (control && control.tagName === 'SELECT') {
        maxQuantity = Math.max(0, ...[...control.options].map(option => Number(option.value) || 0));
      } else if (control) {
        maxQuantity = Number(control.max || control.getAttribute('max')) || 10;
      } else if (plus) {
        maxQuantity = 10;
      } else if (radio && !radioDisabled) {
        // 票區頁只選區域，實際張數會在下一階段決定。
        maxQuantity = 99;
      }
      const name = clean((element.querySelector('[data-ticket-name], .ticket-name, .name, th, td') || element).innerText);
      ticketUnits.push({
        key: keyFor(element, 'ticket', index), name, price_text: text, price,
        status: unusable(text) ? '售完' : '可售',
        available: !unusable(text) && !radioDisabled && maxQuantity > 0,
        max_quantity: maxQuantity,
        general_admission: /自由入座|自由席|站席|搖滾區|無劃位|一般區/i.test(text),
        selection_mode: radio ? 'area' : 'quantity'
      });
    });

  const seatNodes = [...document.querySelectorAll(
    '[data-seat-id], [data-seat-number], [data-seat-no], .seat[data-row], svg [data-seat]'
  )].filter(visible);
  const seats = seatNodes.map((element, index) => {
    const text = clean(element.getAttribute('aria-label') || element.title || element.innerText);
    const classText = clean(element.className && (element.className.baseVal || element.className));
    const seatNumber = clean(element.dataset.seatNumber || element.dataset.seatNo ||
      (text.match(/(?:座號|seat|no\.?)[：:\s]*([0-9]+)/i) || [,''])[1]);
    const row = clean(element.dataset.row || (text.match(/(?:排|row)[：:\s]*([A-Za-z0-9]+)/i) || [,''])[1]);
    const area = clean(element.dataset.area || element.dataset.zone ||
      (element.closest('[data-area], [data-zone]') || {}).dataset?.area || '');
    return {
      key: keyFor(element, 'seat', index), area, row, seat_number: seatNumber,
      price: priceOf(text), available: !unusable(text) && !/disabled|selected/.test(classText),
      selectable: !element.disabled && element.getAttribute('aria-disabled') !== 'true'
    };
  });
  const seatMap = seats.length > 0 || Boolean(document.querySelector('canvas, svg.seat-map, [class*="seat-map"]'));

  const paymentAction = [...document.querySelectorAll('button, a')]
    .filter(visible)
    .some(element => /前往付款|立即付款|選擇付款|信用卡付款|ATM付款/i.test(clean(element.innerText)));
  const orderReview = /訂單明細|票券明細|訂購明細|訂單確認|購票明細/i.test(body);
  const numberAfter = label => {
    const match = body.match(new RegExp('(?:' + label + ')[^0-9]{0,20}([0-9][0-9,]*)', 'i'));
    return match ? Number(match[1].replace(/,/g, '')) : null;
  };
  const selectedName = clean((document.querySelector(
    '[data-selected-ticket-name], .order-ticket-name, .cart-ticket-name, .ticket-name, [class*="ticket-name"]'
  ) || {}).innerText);
  const quantityMatch = body.match(/(?:數量|張數|quantity)[^0-9]{0,12}([0-9]+)/i) || body.match(/x\s*([0-9]+)/i);
  const priceCandidates = [...document.querySelectorAll('body *')]
    .filter(element => {
      const text = clean(element.innerText);
      return visible(element) && text.length <= 100 && /(?:NT(?:\$|\.)?|TWD\$?|\$)\s*[0-9][0-9,]*/i.test(text);
    })
    .slice(0, 12)
    .map(element => ({
      tag: element.tagName,
      class_name: clean(element.className && (element.className.baseVal || element.className)),
      text: clean(element.innerText),
      parent_tag: element.parentElement && element.parentElement.tagName,
      parent_class: clean(element.parentElement && element.parentElement.className &&
        (element.parentElement.className.baseVal || element.parentElement.className)),
      parent_text: clean(element.parentElement && element.parentElement.innerText).slice(0, 300)
    }));
  const orderAreaPanels = [...document.querySelectorAll('.v-expansion-panel, [class~="expansion-panel"]')];
  orderAreaPanels.forEach((element, index) => keyFor(element, 'ticket-area', index));
  const orderAreas = orderAreaPanels.map((element, index) => {
      const header = element.querySelector(':scope > button.v-expansion-panel-header');
      const nestedPanel = element.querySelector(
        ':scope > .v-expansion-panel-content .v-expansion-panel, :scope > [class~="expansion-panel-content"] [class~="expansion-panel"]'
      );
      const ancestorKeys = [];
      let parent = element.parentElement && element.parentElement.closest(
        '.v-expansion-panel, [class~="expansion-panel"]'
      );
      while (parent) {
        ancestorKeys.unshift(parent.dataset.ticketBotKey || '');
        parent = parent.parentElement && parent.parentElement.closest(
          '.v-expansion-panel, [class~="expansion-panel"]'
        );
      }
      return {
        key: keyFor(element, 'ticket-area', index),
        text: clean(element.innerText || element.textContent),
        is_leaf: !nestedPanel,
        ancestor_keys: ancestorKeys.filter(Boolean),
        disabled: !header || header.disabled || header.getAttribute('aria-disabled') === 'true' ||
          /disabled/.test(clean(header.className))
      };
    });

  return {
    page_url: location.href, title: document.title, logged_in: loggedIn,
    login_required: loginRequired, transient_error: transientError,
    sale_not_started: saleNotStarted, queue, sessions, ticket_units: ticketUnits,
    seat_map: seatMap, seats, order_review: orderReview,
    ticket_name: selectedName, quantity: quantityMatch ? Number(quantityMatch[1]) : null,
    subtotal: numberAfter('票款小計|小計|subtotal'), fees: numberAfter('手續費|系統服務費|fees?'),
    total: numberAfter('總計|總額|total'), payment_action: paymentAction,
    verification_required: verificationRequired,
    body_excerpt: body.slice(0, 1200), price_candidates: priceCandidates,
    order_areas: orderAreas
  };
})()
""".replace("__TICKETPLUS_SESSION_SELECTOR__", TICKETPLUS_SESSION_SELECTOR).replace(
    "__TICKETPLUS_TICKET_SELECTOR__", TICKETPLUS_TICKET_SELECTOR
)


class TicketPlusBot:
    """以瀏覽器操作遠大售票，並在付款前停止。"""

    def __init__(
        self,
        config: AppConfig,
        event: EventConfig,
        session: SessionConfig | None = None,
    ):
        self.config = config
        self.event = event
        self.session = session
        self.engine: BrowserEngine = create_engine(config.browser.engine)
        self.page: PageWrapper | None = None
        self.last_success_info = ""
        self.last_error = ""
        self._ticket_plan: dict | None = None
        self._last_snapshot_diagnostic = ""
        self._selected_session_key: str | None = None
        self._lab_http: curl_requests.AsyncSession | None = None

    async def start_browser(self) -> None:
        user_data_dir = self.session.user_data_dir if self.session else self.config.browser.user_data_dir
        await self.engine.launch(
            headless=self.config.browser.headless,
            user_data_dir=user_data_dir,
            executable_path=self.config.browser.executable_path,
            lang=self.config.browser.lang,
            proxy_server=self.session.proxy_server if self.session else "",
        )

    async def _ensure_page(self) -> PageWrapper:
      if self.page is None:
          await self.start_browser()
          self.page = await self.engine.new_page()
          # 加入安全的層級判斷，存取底層原生的 Playwright page
          raw_page = getattr(self.page, "page", None)
          if raw_page and hasattr(raw_page, "add_init_script"):
              await raw_page.add_init_script("""
                  Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
                  window.navigator.chrome = { runtime: {} };
              """)
      return self.page

    async def open_login_page(self) -> None:
        page = await self._ensure_page()
        login_url = (
            self._lab_origins()["web"]
            if is_ticketplus_lab_url(self.event.url)
            else TICKETPLUS_HOME_URL
        )
        await page.goto(login_url)
        logger.info("已開啟 Ticket Plus，請在瀏覽器手動登入；登入狀態會保存在瀏覽器 profile。")

    async def _snapshot(self) -> dict:
        if self.page is None:
            raise RuntimeError("Ticket Plus 頁面尚未建立")
        result = await self.page.evaluate(_SNAPSHOT_JS)
        if not isinstance(result, dict):
            return {}
        if "payment_action" in result:
            page_url = str(result.get("page_url", ""))
            result["payment_boundary"] = is_payment_boundary(
                page_url,
                bool(result.pop("payment_action")),
            )
            if not is_payment_boundary(page_url, True):
                result["order_review"] = False
        if not result.get("ticket_units") and result.get("order_areas"):
            result["ticket_units"] = ticket_units_from_order_areas(
                result["order_areas"]
            )
        return result

    async def _select_session(self, key: str) -> None:
        if self.page is None:
            raise RuntimeError("Ticket Plus 頁面尚未建立")
        key_json = json.dumps(key, ensure_ascii=False)
        script = rf"""
        (() => {{
          window.ticketPlusSelectSession = window.ticketPlusSelectSession || (key => {{
            const row = [...document.querySelectorAll('[data-ticket-bot-key]')]
              .find(element => element.dataset.ticketBotKey === key);
            if (!row) return false;
            const target = row.matches('button, a') ? row : row.querySelector('button, a');
            if (!target || /尚未開賣|售完/.test(target.innerText || row.innerText)) return false;
            target.click(); return true;
          }});
          return window.ticketPlusSelectSession({key_json});
        }})()
        """
        if await self.page.evaluate(script) is not True:
            raise RuntimeError(f"Ticket Plus 無法選擇場次: {key}")

    async def _select_ticket(self, plan: dict) -> None:
        if self.page is None:
            raise RuntimeError("Ticket Plus 頁面尚未建立")

        raw_page = getattr(self.page, "_page", None) or getattr(self.page, "page", None) or self.page
        ticket_key = plan.get("ticket_key", "")
        quantity = plan.get("quantity", 1)
        key_json = json.dumps(ticket_key, ensure_ascii=False)

        # 1. 用 JS 分析 DOM 階層，標記出所有需要由外而內展開的 Header 按鈕
        get_headers_script = rf"""
        (() => {{
          const key = {key_json};
          const target = document.querySelector(`[data-ticket-bot-key="${{key}}"]`);
          if (!target) return [];

          const chain = [];
          let curr = target;

          // 由下往上收集所有父級 v-expansion-panel
          while (curr) {{
            if (curr.classList && curr.classList.contains('v-expansion-panel')) {{
              chain.unshift(curr); // 放在開頭，確保「由外而內」
            }}
            curr = curr.parentElement;
          }}

          // 檢查目標內部是否還包含子 Panel (例如選擇了 3880 區外層，自動選擇裡面的 B1區)
          const childPanels = [...target.querySelectorAll('.v-expansion-panel')];
          if (childPanels.length > 0) {{
            const validChild = childPanels.find(p => {{
              const txt = p.innerText || '';
              return (txt.includes('熱賣中') || txt.includes('剩餘')) && !p.querySelector('.v-chip--disabled');
            }}) || childPanels[0];
            chain.push(validChild);
          }}

          const headersToClick = [];
          for (const panel of chain) {{
            if (panel.getAttribute('aria-expanded') !== 'true') {{
              const btn = panel.querySelector('button.v-expansion-panel-header, .v-expansion-panel-header');
              if (btn) {{
                if (!btn.dataset.ticketBotHeaderId) {{
                  btn.dataset.ticketBotHeaderId = 'hdr-' + Math.random().toString(36).substring(2, 9);
                }}
                headersToClick.push(btn.dataset.ticketBotHeaderId);
              }}
            }}
          }}
          return headersToClick;
        }})()
        """

        # 2. 每次只展開一層。
        # 外層展開後 Vue 會重新建立內層 DOM，所以必須重新取得 snapshot 與 header。
        expanded_panel = False

        for _ in range(8):
            header_ids = await self.page.evaluate(get_headers_script)

            # 外層與目標票區都已展開
            if not header_ids:
                break

            # 每輪只點目前最外層尚未展開的 header
            hdr_id = header_ids[0]
            hdr_locator = raw_page.locator(
                f'[data-ticket-bot-header-id="{hdr_id}"]'
            ).first

            if not await hdr_locator.is_visible():
                # Vue 可能剛完成重新渲染，重新建立 data-ticket-bot-key
                await self._snapshot()
                await self.page.sleep(0.2)
                continue

            await hdr_locator.click(force=True)
            expanded_panel = True

            # 等待 Vuetify 展開動畫與 Vue 重新建立內層 DOM
            await self.page.sleep(0.5)
            await self._snapshot()

        # 3. 調整張數 (+)
        if plan.get("selection_mode") == "area_panel" or expanded_panel:
            for _ in range(30):
                # 只定位目前展開票區內可見的加號，避免抓到其他隱藏票區
                plus_btn = raw_page.locator(
                    ".count-button:visible button:has(.mdi-plus)"
                ).first

                if await plus_btn.is_visible() and await plus_btn.is_enabled():
                    # 檢查當前數量
                    get_qty_script = """
                    (() => {
                      const counters = [...document.querySelectorAll('.count-button')].filter(el => el.offsetParent !== null || el.getClientRects().length > 0);
                      if (counters.length === 0) return 0;
                      const numNode = [...counters[0].children].find(el => el.tagName !== 'BUTTON' && /^\\d+$/.test(el.innerText.trim()));
                      return numNode ? parseInt(numNode.innerText.trim(), 10) : 0;
                    })()
                    """
                    current_qty = await self.page.evaluate(get_qty_script)
                    if current_qty >= quantity:
                        break

                    await plus_btn.click(force=True)
                    await self.page.sleep(0.25)
                else:
                    await self.page.sleep(0.2)

        elif plan.get("selection_mode") == "area":
            radio = raw_page.locator(f'[data-ticket-bot-key="{ticket_key}"] input[type="radio"], [role="radio"]').first
            if await radio.is_visible():
                await radio.click(force=True)

        # 4. 點擊下一步按鈕
        for _ in range(25):
            await self.page.sleep(0.2)
            next_btn = raw_page.locator("button.nextBtn, .nextBtn").first
            if await next_btn.is_visible() and await next_btn.is_enabled():
                await next_btn.click(force=True)
                return

        # 5. 診斷輸出 (若依然未成功啟用下一步)
        diagnostic_script = rf"""
        (() => {{
          const clean = value => String(value || '').replace(/\s+/g, ' ').trim();
          const row = [...document.querySelectorAll('[data-ticket-bot-key]')]
            .find(element => element.dataset.ticketBotKey === {key_json});
          const nextButtons = [...document.querySelectorAll('button, a')]
            .filter(element => /下一步|確認張數|立即訂購|建立訂單/.test(clean(element.innerText)))
            .map(element => ({{
              text: clean(element.innerText),
              disabled: Boolean(element.disabled) || element.getAttribute('aria-disabled') === 'true' ||
                /disabled/.test(clean(element.className)),
              class_name: clean(element.className)
            }}));
          return {{
            selected_row_text: clean(row && row.innerText),
            selected_row_class: clean(row && row.className),
            selected_row_html: String(row && row.outerHTML || '').slice(0, 5000),
            next_buttons: nextButtons
          }};
        }})()
        """
        diagnostic = await self.page.evaluate(diagnostic_script)
        logger.error(
            "Ticket Plus 票區選擇診斷：%s",
            json.dumps(diagnostic, ensure_ascii=False),
        )
        raise RuntimeError("Ticket Plus 已選擇票區，但『下一步』按鈕仍未啟用")

    async def _select_seats(self, seats: list[dict]) -> None:
        if self.page is None:
            raise RuntimeError("Ticket Plus 頁面尚未建立")
        keys = [seat["key"] for seat in seats]
        payload = json.dumps(keys, ensure_ascii=False)
        script = f"""
        (() => {{
          window.ticketPlusSelectSeats = window.ticketPlusSelectSeats || (keys => {{
            const nodes = keys.map(key => [...document.querySelectorAll('[data-ticket-bot-key]')]
              .find(element => element.dataset.ticketBotKey === key));
            if (nodes.some(node => !node)) return false;
            nodes.forEach(node => node.click());
            const next = [...document.querySelectorAll('button, a')].find(el =>
              /確認座位|下一步|建立訂單/.test((el.innerText || '').trim()));
            if (next) next.click();
            return true;
          }});
          return window.ticketPlusSelectSeats({payload});
        }})()
        """
        if await self.page.evaluate(script) is not True:
            raise RuntimeError(f"Ticket Plus 無法選擇座位: {', '.join(keys)}")

    def _lab_origins(self) -> dict[str, str]:
        """依據傳入的 URL 動態切換正式網域與測試網域端點。"""
        parsed = urlparse(self.event.url)
        hostname = (parsed.hostname or "").casefold()
        domain = "ticketplus.com.test" if "ticketplus.com.test" in hostname else "ticketplus.com.tw"
        return {
            "web": f"https://{domain}",
            "config": f"https://apis.{domain}",
            "queue": f"https://queue.{domain}",
            "ticket": f"https://api.{domain}",
        }

    async def _lab_authorization(self) -> str:
        """從已登入的頁面找出前端目前使用的短期 access token。"""
        if self.page is None:
            return ""
        script = r"""
        (() => {
          const found = [];
          const add = (key, value, depth = 0) => {
            if (depth > 4 || value === null || value === undefined) return;
            if (typeof value === 'string') {
              const text = value.trim();
              if (!text) return;
              const tokenKey = /access.?token|authorization/i.test(key);
              const jwt = /^(?:Bearer\s+)?eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$/i.test(text);
              const tokenKey = /access.?token|authorization/i.test(key);
              const jwt =
                /^(?:Bearer\s+)?eyJ[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+){2,4}$/i.test(text);

              if (
                jwt ||
                (
                  tokenKey &&
                  text.length >= 16 &&
                  !/[\r\n]/.test(text)
                )
              ) {
                const score = tokenKey
                  ? 100
                  : (/token/i.test(key) ? 50 : 10);

                found.push({
                  score,
                  value: text
                });
              }
              if (/^[\[{]/.test(text)) {
                try { add(key, JSON.parse(text), depth + 1); } catch (_) {}
              }
              return;
            }
            if (Array.isArray(value)) {
              value.forEach((item, index) => add(`${key}.${index}`, item, depth + 1));
              return;
            }
            if (typeof value === 'object') {
              Object.entries(value).forEach(([childKey, childValue]) =>
                add(`${key}.${childKey}`, childValue, depth + 1));
            }
          };
          [window.localStorage, window.sessionStorage].forEach(storage => {
            for (let index = 0; index < storage.length; index += 1) {
              const key = storage.key(index);
              add(key || '', storage.getItem(key));
            }
          });
          document.cookie
          .split(';')
          .map(item => item.trim())
          .filter(Boolean)
          .forEach(item => {
            const separator = item.indexOf('=');
            if (separator < 0) return;

            const name = item.slice(0, separator).trim();
            let value = item.slice(separator + 1);

            try {
              value = decodeURIComponent(value);
            } catch (_) {}

            add(`cookie.${name}`, value);
          });
          found.sort((left, right) => right.score - left.score);
          if (!found.length) return '';
          return /^Bearer\s+/i.test(found[0].value)
            ? found[0].value
            : `Bearer ${found[0].value}`;
        })()
        """
        result = await self.page.evaluate(script)
        return str(result or "")

    async def _init_lab_http(self) -> None:
        if self.page is None:
            raise RuntimeError("Ticket Plus 頁面尚未建立")
        if self._lab_http is not None:
            await self._close_lab_http()

        cookies = {
            str(item.get("name")): str(item.get("value"))
            for item in await self.page.get_all_cookies()
            if item.get("name") and item.get("value")
        }
        authorization = await self._lab_authorization()
        if not authorization:
            raise RuntimeError("Ticket Plus 找不到登入 access token，請先完成登入")
        user_agent = await self.page.evaluate("navigator.userAgent")
        origins = self._lab_origins()
        self._lab_http = curl_requests.AsyncSession(
            impersonate="chrome124",
            cookies=cookies,
            headers={
                "Accept": "application/json, text/plain, */*",
                "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
                "Authorization": authorization,
                "Content-Type": "application/json",
                "Origin": origins["web"],
                "Referer": f'{origins["web"]}/',
                "User-Agent": str(user_agent or "Mozilla/5.0"),
            },
            timeout=15,
        )

    async def _close_lab_http(self) -> None:
        if self._lab_http is None:
            return
        try:
            close = getattr(self._lab_http, "aclose", None)
            if close is not None:
                await close()
            else:
                result = self._lab_http.close()
                if asyncio.iscoroutine(result):
                    await result
        finally:
            self._lab_http = None

    async def _lab_request(
        self,
        method: str,
        url: str,
        *,
        params: dict | None = None,
        body: dict | None = None,
    ) -> dict:
        if not is_ticketplus_lab_url(url):
            raise RuntimeError(f"Ticket Plus 拒絕連線至未授權網域: {url}")
        if self._lab_http is None:
            raise RuntimeError("Ticket Plus HTTP session 尚未初始化")
        response = await self._lab_http.request(
            method,
            url,
            params=params,
            json=body,
        )
        if response.status_code < 200 or response.status_code >= 300:
            raise RuntimeError(
                f"Ticket Plus API HTTP {response.status_code}: {urlparse(url).path}"
            )
        try:
            payload = response.json()
        except (TypeError, ValueError) as error:
            raise RuntimeError("Ticket Plus API 回傳無效 JSON") from error
        if not isinstance(payload, (dict, str)):
            raise RuntimeError("Ticket Plus API 回傳格式無效")
        return payload

    async def _lab_s3(self, activity_id: str, filename: str) -> dict:
        origins = self._lab_origins()
        payload = await self._lab_request(
            "GET",
            f'{origins["config"]}/config/api/v1/getS3',
            params={"path": f"event/{activity_id}/{filename}"},
        )
        return parse_api_document(payload)

    async def _lab_enqueue(self, request_body: dict) -> str:
        origins = self._lab_origins()
        deadline = asyncio.get_running_loop().time() + LAB_QUEUE_TIMEOUT_SECONDS
        while asyncio.get_running_loop().time() < deadline:
            payload = await self._lab_request(
                "POST",
                f'{origins["queue"]}/queue/api/v1/enqueue',
                params={"_": int(time.time() * 1000)},
                body=request_body,
            )
            err_code = str(payload.get("errCode", ""))
            uuid = str(payload.get("uuid") or "")
            if err_code == "00" and uuid:
                return uuid
            if err_code == "137" or payload.get("waitSecond") is not None:
                wait_seconds = max(1, min(60, int(payload.get("waitSecond") or 1)))
                logger.info("Ticket Plus 排隊中，%d 秒後重試。", wait_seconds)
                await asyncio.sleep(wait_seconds)
                continue
            message = str(payload.get("errMsg") or payload.get("errDetail") or err_code)
            raise RuntimeError(f"Ticket Plus enqueue 失敗: {message}")
        raise RuntimeError("Ticket Plus 排隊等待超過 10 分鐘")

    async def _run_lab_flow(self) -> bool:
        if not is_ticketplus_lab_url(self.event.url):
            raise RuntimeError("Ticket Plus 網址格式無效")
        activity_id = extract_activity_id(self.event.url)
        if not activity_id:
            raise RuntimeError("Ticket Plus 活動網址格式無效")

        page = await self._ensure_page()
        await page.goto(self.event.url)
        await page.sleep(0.5)
        await self._init_lab_http()

        sessions_doc, areas_doc, products_doc = await asyncio.gather(
            self._lab_s3(activity_id, "sessions.json"),
            self._lab_s3(activity_id, "ticketAreas.json"),
            self._lab_s3(activity_id, "products.json"),
        )
        sessions = sessions_doc.get("sessions", [])
        ticket_areas = areas_doc.get("ticketAreas", [])
        products = products_doc.get("products", [])
        if not all(isinstance(items, list) for items in (sessions, ticket_areas, products)):
            raise RuntimeError("Ticket Plus 活動設定格式不完整")

        area_ids = [
            str(item.get("ticketAreaId"))
            for item in ticket_areas
            if item.get("ticketAreaId")
        ]
        product_ids = [
            str(item.get("productId"))
            for item in products
            if item.get("productId")
        ]
        origins = self._lab_origins()
        live_payload = await self._lab_request(
            "GET",
            f'{origins["config"]}/config/api/v1/get',
            params={
                "ticketAreaId": ",".join(area_ids),
                "productId": ",".join(product_ids),
                "_": int(time.time() * 1000),
            },
        )
        live_result = parse_api_document(live_payload)
        plan = build_lab_ticket_plan(
            self.event,
            sessions,
            ticket_areas,
            products,
            live_result,
        )
        self._ticket_plan = {
            "ticket_name": plan["ticket_name"],
            "quantity": plan["quantity"],
            "price": plan["price"],
        }

        order_url = (
            f'{origins["web"]}/order/{activity_id}/{plan["session_id"]}'
        )
        await page.goto(order_url)
        await page.sleep(0.5)
        request_body = {
            "consecutiveSeats": False,
            "finalizedSeats": True,
            "products": [
                {"count": plan["quantity"], "productId": plan["product_id"]}
            ],
            "reserveSeats": True,
        }
        queue_uuid = await self._lab_enqueue(request_body)
        reserve_payload = await self._lab_request(
            "POST",
            f'{origins["ticket"]}/ticket/api/v1/reserve',
            params={"_": int(time.time() * 1000)},
            body={**request_body, "uuid": queue_uuid},
        )
        reserve = parse_api_document(reserve_payload)
        if not reserve.get("orderId"):
            raise RuntimeError("Ticket Plus reserve 成功回應缺少 orderId")

        await page.goto(order_url)
        await page.sleep(1.0)
        self.last_success_info = (
            f"票區: {plan['ticket_name']}；張數: {plan['quantity']}；"
            f"總額: {reserve.get('total')}；已停在付款前。"
        )
        logger.info("Ticket Plus 訂單建立成功，%s", self.last_success_info)
        return True

    async def run(self) -> bool:
        try:
            if is_ticketplus_lab_url(self.event.url):
                return await self._run_lab_flow()
            page = await self._ensure_page()
            await page.goto(self.event.url)
            self._selected_session_key = None
            state_steps = 0
            queue_steps = 0
            queue_logged = False
            while state_steps < MAX_STATE_STEPS:
                snapshot = await self._snapshot()
                if snapshot.get("verification_required"):
                    state_steps += 1
                    logger.warning("Ticket Plus 等待使用者完成人工驗證，程式不會嘗試繞過。")
                    await page.sleep(STATE_POLL_SECONDS)
                    continue

                state = detect_page_state(snapshot)
                if state == "transient_error":
                    self.last_error = "Ticket Plus 排隊或送出訂單時發生錯誤：您的操作好像出了一點問題，請稍後再試。"
                    logger.error(self.last_error)
                    return False
                if state == "login_required":
                    self.last_error = "Ticket Plus 尚未登入；請先執行 login --platform ticketplus。"
                    logger.error(self.last_error)
                    return False
                if state == "queue":
                    queue_steps += 1
                    if not queue_logged:
                        logger.info("Ticket Plus 已進入官方排隊頁面，等待網站自動放行。")
                        queue_logged = True
                    if queue_steps >= MAX_QUEUE_STEPS:
                        self.last_error = "Ticket Plus 官方排隊等待超過 10 分鐘。"
                        logger.error(self.last_error)
                        return False
                    await page.sleep(STATE_POLL_SECONDS)
                    continue
                state_steps += 1
                if state == "sale_not_started":
                    await page.sleep(STATE_POLL_SECONDS)
                    continue
                if state == "session_select":
                    if self._selected_session_key is not None:
                        await page.sleep(STATE_POLL_SECONDS)
                        continue
                    logger.info(
                        "Ticket Plus 場次篩選資料：date_keyword=%r sessions=%s",
                        self.event.date_keyword,
                        json.dumps(snapshot["sessions"], ensure_ascii=False),
                    )
                    session = select_session(self.event, snapshot["sessions"])
                    await self._select_session(session["key"])
                    self._selected_session_key = session["key"]
                    logger.info("Ticket Plus 已送出場次選擇，等待訂票頁面載入。")
                    await page.sleep(STATE_POLL_SECONDS)
                    continue
                if state == "ticket_select":
                    try:
                        self._ticket_plan = build_ticket_plan(self.event, snapshot["ticket_units"])
                    except ValueError as error:
                        self.last_error = str(error)
                        await page.sleep(STATE_POLL_SECONDS)
                        continue
                    await self._select_ticket(self._ticket_plan)
                    await page.sleep(STATE_POLL_SECONDS)
                    continue
                if state == "seat_map":
                    if not snapshot.get("seats"):
                        self.last_error = "Ticket Plus 座位圖只有 Canvas 或沒有可讀座位資料，已安全停止。"
                        logger.error(self.last_error)
                        return False
                    selected = select_adjacent_seats(self.event, snapshot["seats"])
                    await self._select_seats(selected)
                    await page.sleep(STATE_POLL_SECONDS)
                    continue
                # 只要處於選區/結帳狀態，或者網址在 /order/ 或 /confirm/，就強制重置超時計數器
                current_url = str(snapshot.get("page_url", ""))
                parsed_path = urlparse(current_url).path

                if state in {
                    "session_select",
                    "ticket_select",
                    "seat_map",
                    "order_review",
                    "payment_boundary",
                } or "/order/" in parsed_path or "/confirm/" in parsed_path:
                    state_steps = 0 # ◄ 強制重置計數器，不讓它 20 秒死機

                current_url = str(snapshot.get("page_url", ""))
                is_on_confirm_page = "/confirm/" in current_url or "/checkout/" in current_url or "/payment/" in current_url
                
                if is_on_confirm_page or (state in {"order_review", "payment_boundary"} and "/order/" not in current_url):
                    logger.info("🎉 恭喜！已成功搶到票並進入『確認資料 / 購票明細』頁面！")
                    logger.info("💡 瀏覽器已永久保持開啟，請直接在視窗中完成剩餘資料填寫與付款。完成後可按 Ctrl+C 結束。")
                    self.last_success_info = "已進入訂單頁面，瀏覽器保持開啟中。"
                    
                    # 進入無限掛機模式，鎖定視窗不關閉
                    while True:
                        await page.sleep(1.0)

                if not self.last_error:
                    logger.info("Ticket Plus 頁面仍在載入，等待 Vue 完成顯示活動內容。")
                page_url = str(snapshot.get("page_url", ""))
                if "/order/" in urlparse(page_url).path:
                    diagnostic = json.dumps(
                        {
                            "page_url": page_url,
                            "title": snapshot.get("title"),
                            "body_excerpt": snapshot.get("body_excerpt"),
                            "price_candidates": snapshot.get("price_candidates", []),
                            "sessions": snapshot.get("sessions", []),
                            "ticket_units": snapshot.get("ticket_units", []),
                            "order_areas": snapshot.get("order_areas", []),
                        },
                        ensure_ascii=False,
                    )
                    if diagnostic != self._last_snapshot_diagnostic:
                        logger.info("Ticket Plus 訂票頁 DOM 診斷：%s", diagnostic)
                        self._last_snapshot_diagnostic = diagnostic
                self.last_error = "Ticket Plus 頁面仍在載入或目前狀態無法辨識。"
                await page.sleep(STATE_POLL_SECONDS)
                continue

            self.last_error = "Ticket Plus 等待頁面狀態逾時。"
            logger.error(self.last_error)
            return False
        except Exception as error:
            self.last_error = str(error)
            logger.exception("Ticket Plus 執行失敗")
            return False

    async def watch(self, interval: float = 5.0) -> bool:
        while True:
            if await self.run():
                return True
            if self.last_error and any(
                marker in self.last_error
                for marker in ("尚未登入", "Canvas", "無法辨識", "訂單核對", "沒有可供核對")
            ):
                return False
            if self.page is not None:
                await self.page.sleep(interval)

    async def close(self) -> None:
        await self._close_lab_http()
        await self.engine.close()
