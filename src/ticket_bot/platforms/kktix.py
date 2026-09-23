"""KKTIX platform skeleton.

This module intentionally focuses on the parts we understand today:
- public organizer event pages on *.kktix.cc
- login / registration entry on kktix.com
- Cloudflare challenge detection before any purchase flow

The purchase, seat-map, and payment flow are left as explicit TODOs until we
finish mapping a live registrations/new DOM and request sequence.
"""

from __future__ import annotations

import json
import logging
import re

from ticket_bot.browser import BrowserEngine, PageWrapper, create_engine
from ticket_bot.config import AppConfig, EventConfig, KKTIXAutofillConfig, SessionConfig
from ticket_bot.platforms.kktix_parser import (
    build_registration_url,
    parse_event_page,
    parse_order_page,
    parse_registration_page,
)

logger = logging.getLogger(__name__)

KKTIX_SIGN_IN_URL = "https://kktix.com/users/sign_in"

# Public organizer event page selectors.
SEL_EVENT_TITLE = ".header-title h1"
SEL_EVENT_BUY_LINK = ".order-now-section a.btn-point, #order-now a"
SEL_EVENT_ORGANIZER = ".organizers a"
SEL_EVENT_TIME = ".event-info .timezoneSuffix"

# Verified selectors for the purchase entry page.
SEL_REG_APP = "#registrationsNewApp"
SEL_REG_TICKET_BLOCK = ".ticket-list .ticket-unit"
SEL_REG_TERMS_CHECKBOX = "#person_agree_terms"
SEL_REG_PICK_SEATS = "button[ng-click='challenge()']"
SEL_REG_BEST_AVAILABLE = "button[ng-click='challenge(1)']"
SEL_ORDER_CANCEL_TICKET = "a.reselect-ticket"
SEL_ORDER_CONFIRM_FORM = "[ng-click='confirmOrder()']"

_GENDER_ALIASES = {
    "male": ["男", "male", "m"],
    "男": ["男", "male", "m"],
    "female": ["女", "female", "f"],
    "女": ["女", "female", "f"],
}

_REGION_ALIASES = {
    "taipei": ["北北基宜地區", "北北基宜", "台北", "taipei"],
    "台北": ["北北基宜地區", "北北基宜", "台北", "taipei"],
    "新北": ["北北基宜地區", "北北基宜", "台北", "新北市", "new taipei"],
    "新北市": ["北北基宜地區", "北北基宜", "台北", "新北市", "new taipei"],
    "new taipei": ["北北基宜地區", "北北基宜", "台北", "新北市", "new taipei"],
    "taoyuan": ["桃竹苗地區", "桃竹苗", "taoyuan"],
    "taichung": ["中彰投地區", "中彰投", "taichung"],
    "tainan": ["雲嘉南地區", "雲嘉南", "tainan"],
    "kaohsiung": ["高屏地區", "高屏", "kaohsiung"],
    "hualien": ["花東地區", "花東", "hualien"],
    "penghu": ["澎金馬地區", "澎金馬", "penghu"],
    "hong kong": ["香港", "hong kong"],
    "hongkong": ["香港", "hong kong"],
    "macau": ["澳門", "macau"],
    "other": ["其他地區", "other"],
}


def _clean_autofill_text(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def _normalize_birth_date(value: str) -> str:
    raw = _clean_autofill_text(value)
    if not raw:
        return ""
    digits = re.sub(r"[^0-9]", "", raw)
    if len(digits) == 8:
        return f"{digits[:4]}/{digits[4:6]}/{digits[6:8]}"
    return raw.replace("-", "/")


def _build_select_candidates(value: str, aliases: dict[str, list[str]]) -> list[str]:
    raw = _clean_autofill_text(value)
    if not raw:
        return []
    candidates: list[str] = []
    seen: set[str] = set()

    def add(candidate: str) -> None:
        normalized = _clean_autofill_text(candidate)
        if not normalized:
            return
        key = normalized.lower()
        if key in seen:
            return
        seen.add(key)
        candidates.append(normalized)

    add(raw)
    for candidate in aliases.get(raw.lower(), []):
        add(candidate)
    return candidates


def _pick_indexed(values: list[str], index: int) -> str:
    cleaned = [_clean_autofill_text(value) for value in values if _clean_autofill_text(value)]
    if not cleaned:
        return ""
    if index < len(cleaned):
        return cleaned[index]
    return cleaned[0]


def _infer_attendee_count(order_info: dict) -> int:
    indexes: set[int] = set()
    for field_name in order_info.get("attendee_field_names", []):
        match = re.match(r"attendees\[(\d+)\]", field_name)
        if match:
            indexes.add(int(match.group(1)))
    if indexes:
        return max(indexes) + 1
    return 1


def build_order_autofill_plan(
    autofill: KKTIXAutofillConfig,
    *,
    attendee_count: int = 1,
) -> dict:
    attendee_count = max(1, attendee_count)
    attendees = []
    for index in range(attendee_count):
        attendees.append(
            {
                "name": _pick_indexed(autofill.attendee_names, index) or _clean_autofill_text(autofill.contact_name),
                "phone": _pick_indexed(autofill.attendee_phones, index) or _clean_autofill_text(autofill.contact_phone),
                "id_number": _pick_indexed(autofill.attendee_id_numbers, index),
                "agree_real_name": bool(autofill.agree_real_name),
            }
        )

    return {
        "enabled": bool(autofill.enabled),
        "contact": {
            "name": _clean_autofill_text(autofill.contact_name),
            "email": _clean_autofill_text(autofill.contact_email),
            "phone": _clean_autofill_text(autofill.contact_phone),
            "birth_date": _normalize_birth_date(autofill.contact_birth_date),
            "gender_candidates": _build_select_candidates(autofill.contact_gender, _GENDER_ALIASES),
            "region_candidates": _build_select_candidates(autofill.contact_region, _REGION_ALIASES),
        },
        "attendees": attendees,
        "options": {
            "display_public_attendance": bool(autofill.display_public_attendance),
            "join_organizer_fan": bool(autofill.join_organizer_fan),
        },
    }


def _summarize_ticket_unit(unit: dict) -> str:
    parts = [
        _clean_autofill_text(unit.get("name", "")),
        _clean_autofill_text(unit.get("label", "")),
        _clean_autofill_text(unit.get("price", "")),
        _clean_autofill_text(unit.get("status", "")),
    ]
    return " / ".join(part for part in parts if part)


def build_registration_selection_plan(event: EventConfig, registration_info: dict) -> dict:
    ticket_units = registration_info.get("ticket_units", [])
    selectable_units = [
        unit
        for unit in ticket_units
        if unit.get("status") == "available" and unit.get("selectable")
    ]
    if not selectable_units:
        raise ValueError("KKTIX 目前沒有可選的票種")

    actions = registration_info.get("action_buttons", [])
    action_text = " ".join(actions).casefold()

    has_best_available = bool(
        registration_info.get("has_best_available_action")
    ) or any(
        keyword in action_text
        for keyword in (
            "best available",
            "電腦配位",
            "系統配位",
            "自動配位",
        )
    )

    has_pick_seat = bool(
        registration_info.get("has_pick_seat_action")
    ) or any(
        keyword in action_text
        for keyword in (
            "pick your seat",
            "自行選位",
            "選擇座位",
        )
    )

    has_next = any(
        keyword in action_text
        for keyword in (
            "下一步",
            "繼續",
            "next",
            "continue",
        )
    )

    explicit_pick_seat = any(
        keyword in action_text
        for keyword in (
            "pick your seat",
            "自行選位",
            "選擇座位",
        )
    )

    if has_best_available:
        action = "best_available"
        action_selector = SEL_REG_BEST_AVAILABLE

    elif has_pick_seat and has_next:
        # KKTIX 的「下一步」也可能使用 challenge()，
        # 不能直接視為人工選位。
        action = "next"
        action_selector = SEL_REG_PICK_SEATS

    elif has_pick_seat and explicit_pick_seat:
        raise ValueError(
            "KKTIX 此活動需要進入座位圖自行選位，"
            "目前尚未實作自動選位"
        )

    else:
        raise ValueError(
            "KKTIX 註冊頁找不到可送出的下一步按鈕；"
            f"解析到的按鈕文字={actions!r}"
        )

    keyword = _clean_autofill_text(event.area_keyword)
    target_units = selectable_units
    if keyword:
        keyword_lower = keyword.casefold()
        target_units = [
            unit
            for unit in selectable_units
            if keyword_lower in _summarize_ticket_unit(unit).casefold()
        ]
        if not target_units:
            available = ", ".join(_summarize_ticket_unit(unit) for unit in selectable_units[:5])
            raise ValueError(f"KKTIX 找不到符合票種關鍵字的可選票: {keyword} ({available})")

    target = target_units[0]
    ticket_id = _clean_autofill_text(target.get("ticket_id", ""))
    if not ticket_id:
        raise ValueError("KKTIX 票種缺少 ticket_id，無法送出 reserve")

    return {
        "ticket_id": ticket_id,
        "ticket_name": _clean_autofill_text(target.get("name", "")),
        "ticket_label": _clean_autofill_text(target.get("label", "")),
        "ticket_price": _clean_autofill_text(target.get("price", "")),
        "quantity": max(1, int(event.ticket_count or 1)),
        "terms_checkbox_id": registration_info.get("terms_checkbox_id") or SEL_REG_TERMS_CHECKBOX.lstrip("#"),
        "action": action,
        "action_selector": action_selector,
        "matched_by_keyword": bool(keyword),
        "available_ticket_summaries": [_summarize_ticket_unit(unit) for unit in selectable_units],
    }


def _registration_dom_ready(registration_info: dict) -> bool:
    # 驗證頁、導頁途中，都不算購票頁載入完成
    if (
        registration_info.get("challenge")
        or not registration_info.get("is_registration_page")
    ):
        return False

    action_buttons = registration_info.get("action_buttons", [])

    # 仍看到 Angular 模板，代表尚未 hydration
    if action_buttons and any(
        "{{" in action or "}}" in action
        for action in action_buttons
    ):
        return False

    ticket_units = registration_info.get("ticket_units", [])

    # 尚未建立票種 DOM
    if not ticket_units:
        return False

    available_units = [
        unit
        for unit in ticket_units
        if unit.get("status") == "available"
    ]

    # 有可售票種，但加減按鈕尚未建立
    if available_units and not any(
        unit.get("selectable") or unit.get("has_plus_button")
        for unit in available_units
    ):
        return False

    return True


class KKTIXBot:
    """KKTIX browser-first bot.

    The current safe scope is:
    - load `registrations/new`
    - reserve via `Best Available`
    - land on reserved order page
    - autofill order form
    - stop before `Confirm Form` / payment
    """

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
        self.last_success_info: str = ""

    async def start_browser(self) -> None:
        user_data_dir = self.session.user_data_dir if self.session else self.config.browser.user_data_dir
        await self.engine.launch(
            headless=self.config.browser.headless,
            user_data_dir=user_data_dir,
            executable_path=self.config.browser.executable_path,
            lang=self.config.browser.lang,
            proxy_server=self.session.proxy_server if self.session else "",
        )
        logger.info("KKTIX 瀏覽器啟動完成 (engine=%s)", self.config.browser.engine)

    async def _ensure_page(self) -> None:
        if self.page is None:
            await self.start_browser()
            self.page = await self.engine.new_page()

    async def open_event_page(self) -> None:
        await self._ensure_page()
        await self.page.goto(self.event.url)

    async def inspect_event_page(self) -> dict:
        """
        Load the public event page and extract stable metadata.

        This is the safest first step for KKTIX because *.kktix.cc pages are
        accessible even when kktix.com purchase pages are challenge-protected.
        """
        await self.open_event_page()
        html = await self.page.evaluate("document.documentElement.outerHTML")
        metadata = parse_event_page(str(html), self.event.url)
        logger.info("KKTIX event page 解析完成: %s", metadata.get("title", ""))
        return metadata

    async def open_registration_page(self) -> None:
        await self._ensure_page()
        url = self.event.url
        if "/registrations/new" not in url:
            url = build_registration_url(url) or url
        await self.page.goto(url)

    async def _current_html(self) -> str:
        await self._ensure_page()
        return str(await self.page.evaluate("document.documentElement.outerHTML"))

    async def _parse_current_registration_page(self) -> dict:
        current_url = await self.page.current_url()
        html = await self._current_html()
        return parse_registration_page(html, current_url)

    async def _parse_current_order_page(self) -> dict:
        current_url = await self.page.current_url()
        html = await self._current_html()
        return parse_order_page(html, current_url)

    async def _ensure_registration_ready(self) -> dict:
        await self.open_registration_page()

        challenge_handled = False
        info: dict = {}

        # 每 0.25 秒檢查一次，最多約 10 秒。
        # 一旦票種渲染完成會立即繼續，不會固定等待 10 秒。
        for _ in range(40):
            current_url = await self.page.current_url()

            if "/users/sign_in" in current_url:
                raise RuntimeError(
                    "KKTIX session 已失效，請重新登入"
                )

            info = await self._parse_current_registration_page()

            if _registration_dom_ready(info):
                return info

            if info.get("challenge") and not challenge_handled:
                challenge_handled = True
                await self.page.handle_cloudflare(timeout=20.0)

            await self.page.sleep(0.25)

        if info.get("challenge"):
            raise RuntimeError(
                "KKTIX 仍停在安全驗證頁，"
                "請先手動完成 challenge / 登入"
            )

        if not info.get("is_registration_page"):
            raise RuntimeError(
                "KKTIX 目前不是 registrations/new 頁面: "
                f"{await self.page.current_url()}"
            )

        raise RuntimeError(
            "KKTIX registrations/new 在 10 秒內未完成渲染；"
            f"title={info.get('title')!r}, "
            f"ticket_units={len(info.get('ticket_units', []))}, "
            f"buttons={info.get('action_buttons', [])!r}"
        )

    async def inspect_registration_page(self) -> dict:
        """
        Load the live registrations/new page and extract stable purchase-entry
        structure such as ticket units, terms checkbox, seat-selection actions,
        and queue/recaptcha hints.
        """
        metadata = await self._ensure_registration_ready()
        logger.info(
            "KKTIX registrations/new 解析完成: %s (%s ticket units)",
            metadata.get("title", ""),
            len(metadata.get("ticket_units", [])),
        )
        logger.info(
            "KKTIX 購票動作: buttons=%r best_available=%s pick_seat=%s",
            metadata.get("action_buttons", []),
            metadata.get("has_best_available_action"),
            metadata.get("has_pick_seat_action"),
        )
        for index, unit in enumerate(metadata.get("ticket_units", [])):
            logger.info(
                "KKTIX 票種[%d]: name=%r price=%r status=%r selectable=%r id=%r",
                index,
                unit.get("name") or unit.get("title"),
                unit.get("price"),
                unit.get("status"),
                unit.get("selectable"),
                unit.get("id"),
            )
        return metadata

    async def inspect_order_page(self) -> dict:
        """
        Inspect a reserved order page after queue / seat selection.
        """
        await self._ensure_page()
        metadata = await self._parse_current_order_page()
        logger.info(
            "KKTIX reserved order 解析完成: pending=%s",
            metadata.get("flags", {}).get("is_reserved_pending"),
        )
        return metadata

    async def _wait_for_order_page(self, timeout: float = 20.0) -> dict:
        await self._ensure_page()
        elapsed = 0.0
        while elapsed < timeout:
            current_url = await self.page.current_url()
            if "/users/sign_in" in current_url:
                raise RuntimeError("KKTIX session 已失效，請重新登入")
            if "/registrations/" in current_url and "/registrations/new" not in current_url:
                await self.page.sleep(0.4)
                order_info = await self._parse_current_order_page()
                if order_info.get("is_order_page"):
                    return order_info
            await self.page.sleep(0.25)
            elapsed += 0.25
        raise TimeoutError("KKTIX reserve 後未能進入 order 頁")

    async def reserve_tickets(self, registration_info: dict | None = None) -> dict:
        await self._ensure_page()
        if registration_info is None:
            registration_info = await self._ensure_registration_ready()

        plan = build_registration_selection_plan(self.event, registration_info)
        payload = json.dumps(plan, ensure_ascii=False)
        result = await self.page.evaluate(
            f"""
            (() => {{
              const plan = {payload};
              const result = {{
                selected: false,
                clicked_terms: false,
                clicked_action: false,
                action_disabled_before_click: false,
                selected_quantity: 0,
                errors: [],
              }};

              const fire = (el) => {{
                el.dispatchEvent(new Event('input', {{ bubbles: true }}));
                el.dispatchEvent(new Event('change', {{ bubbles: true }}));
                el.dispatchEvent(new Event('blur', {{ bubbles: true }}));
              }};
              const block = document.querySelector(`#ticket_${{plan.ticket_id}}`);
              if (!block) {{
                result.errors.push(`找不到 ticket_${{plan.ticket_id}}`);
                return result;
              }}

              const input = block.querySelector("input[type='text']");
              const plus = block.querySelector("button.plus, .btn-default.plus");
              const minus = block.querySelector("button.minus, .btn-default.minus");
              const readCount = () => {{
                const raw = parseInt((input && input.value) || '0', 10);
                return Number.isFinite(raw) ? raw : 0;
              }};

              let current = readCount();
              for (let i = 0; i < 20 && current < plan.quantity; i += 1) {{
                if (!plus || plus.disabled) break;
                plus.click();
                current = readCount();
              }}
              for (let i = 0; i < 20 && current > plan.quantity; i += 1) {{
                if (!minus || minus.disabled) break;
                minus.click();
                current = readCount();
              }}
              if (input && current !== plan.quantity) {{
                input.focus();
                input.value = String(plan.quantity);
                fire(input);
                current = plan.quantity;
              }}
              result.selected_quantity = current;
              result.selected = current === plan.quantity;
              if (!result.selected) {{
                result.errors.push(`票數設定失敗: expected=${{plan.quantity}} actual=${{current}}`);
              }}

              const terms = document.getElementById(plan.terms_checkbox_id);
              if (!terms) {{
                result.errors.push(`找不到條款 checkbox: ${{plan.terms_checkbox_id}}`);
              }} else {{
                if (!terms.checked) {{
                  terms.click();
                }} else {{
                  fire(terms);
                }}
                result.clicked_terms = !!terms.checked;
              }}

              const actionButton = document.querySelector(plan.action_selector);
              if (!actionButton) {{
                result.errors.push(`找不到 action button: ${{plan.action_selector}}`);
                return result;
              }}
              result.action_disabled_before_click = !!actionButton.disabled;

              const actionScope = window.angular?.element ? window.angular.element(actionButton).scope() : null;
              try {{
                if (actionScope && typeof actionScope.challenge === 'function') {{
                  actionScope.challenge(plan.action === 'best_available' ? 1 : undefined);
                  if (!actionScope.$$phase && typeof actionScope.$apply === 'function') {{
                    actionScope.$apply();
                  }}
                }} else {{
                  actionButton.click();
                }}
                result.clicked_action = true;
              }} catch (error) {{
                result.errors.push(String(error));
              }}

              return result;
            }})()
            """
        )
        if result.get("errors"):
            raise RuntimeError("KKTIX reserve 準備失敗: " + "; ".join(result["errors"]))

        order_info = await self._wait_for_order_page()
        return {
            "plan": plan,
            "submit_result": result,
            "order_info": order_info,
        }

    async def autofill_order_form(self) -> dict:
        """
        Fill a reserved order page with KKTIX autofill profile data.

        This only fills contact/attendee fields and toggles required checkboxes.
        It does not click the final confirm or payment button.
        """
        if not self.config.kktix.enabled:
            raise RuntimeError("KKTIX autofill 未啟用；請先在 config 設定 kktix.enabled: true")

        await self._ensure_page()
        if self.page is None:
            raise RuntimeError("KKTIX page 尚未初始化")

        current_url = await self.page.current_url()
        html = await self.page.evaluate("document.documentElement.outerHTML")
        order_info = parse_order_page(str(html), current_url)
        if not order_info.get("is_order_page"):
            raise RuntimeError("目前不是 KKTIX reserved order 頁面，無法進行 autofill")

        plan = build_order_autofill_plan(
            self.config.kktix,
            attendee_count=_infer_attendee_count(order_info),
        )
        payload = json.dumps(plan, ensure_ascii=False)
        result = await self.page.evaluate(
            f"""
            (() => {{
              const plan = {payload};
              const result = {{
                applied: [],
                missing_required: [],
                warnings: [],
              }};

              const clean = (value) => (value || '').replace(/\\s+/g, ' ').trim();
              const fire = (el) => {{
                el.dispatchEvent(new Event('input', {{ bubbles: true }}));
                el.dispatchEvent(new Event('change', {{ bubbles: true }}));
                el.dispatchEvent(new Event('blur', {{ bubbles: true }}));
              }};
              const setText = (el, value) => {{
                if (!el || !value) return false;
                el.focus();
                el.value = value;
                fire(el);
                return true;
              }};
              const setCheckbox = (el, checked) => {{
                if (!el) return false;
                if (!!el.checked !== !!checked) {{
                  el.click();
                }} else {{
                  fire(el);
                }}
                return true;
              }};
              const setRadio = (el) => {{
                if (!el) return false;
                if (!el.checked) {{
                  el.click();
                }} else {{
                  fire(el);
                }}
                return true;
              }};
              const setSelectByText = (el, candidates) => {{
                if (!el || !Array.isArray(candidates) || candidates.length === 0) return false;
                const normalized = candidates.map((item) => clean(item).toLowerCase()).filter(Boolean);
                const options = [...el.options];
                for (const option of options) {{
                  const text = clean(option.textContent).toLowerCase();
                  if (normalized.includes(text)) {{
                    el.value = option.value;
                    fire(el);
                    return true;
                  }}
                }}
                for (const option of options) {{
                  const text = clean(option.textContent).toLowerCase();
                  if (normalized.some((candidate) => text.includes(candidate) || candidate.includes(text))) {{
                    el.value = option.value;
                    fire(el);
                    return true;
                  }}
                }}
                return false;
              }};
              const controlOf = (el) => el.closest('.control-group') || el.parentElement;
              const labelOf = (el) => {{
                const control = controlOf(el);
                if (!control) return '';
                const label = control.querySelector('.control-label');
                return clean(label ? label.innerText : control.innerText);
              }};
              const requiredOf = (el) => {{
                const control = controlOf(el);
                return !!(control && control.querySelector('abbr[title=\"required\"]'));
              }};
              const pushApplied = (name, label, value) => {{
                result.applied.push({{ name, label, value }});
              }};
              const pushMissing = (name, label) => {{
                if (!result.missing_required.some((item) => item.name === name)) {{
                  result.missing_required.push({{ name, label }});
                }}
              }};

              const contactFields = [...document.querySelectorAll(\"input[name^='contact['], select[name^='contact['], textarea[name^='contact[']\")];
              for (const el of contactFields) {{
                if (el.type === 'hidden' || el.type === 'file') continue;
                const name = el.getAttribute('name') || '';
                const label = labelOf(el);
                let applied = false;
                let usedValue = '';

                if (/email/i.test(label) || name.includes('[field_email_')) {{
                  usedValue = plan.contact.email;
                  applied = setText(el, usedValue);
                }} else if (label.includes('姓名')) {{
                  usedValue = plan.contact.name;
                  applied = setText(el, usedValue);
                }} else if (label.includes('手機')) {{
                  usedValue = plan.contact.phone;
                  applied = setText(el, usedValue);
                }} else if (label.includes('生理性別')) {{
                  usedValue = plan.contact.gender_candidates.join(' / ');
                  applied = setSelectByText(el, plan.contact.gender_candidates);
                }} else if (label.includes('出生')) {{
                  usedValue = plan.contact.birth_date;
                  applied = setText(el, usedValue);
                }} else if (label.includes('居住地')) {{
                  usedValue = plan.contact.region_candidates.join(' / ');
                  applied = setSelectByText(el, plan.contact.region_candidates);
                }}

                if (applied) {{
                  pushApplied(name, label, usedValue);
                }} else if (requiredOf(el)) {{
                  pushMissing(name, label);
                }}
              }}

              const attendeeFields = [...document.querySelectorAll(\"input[name^='attendees['], select[name^='attendees['], textarea[name^='attendees[']\")];
              for (const el of attendeeFields) {{
                if (el.type === 'hidden' || el.type === 'file') continue;
                const name = el.getAttribute('name') || '';
                const match = name.match(/^attendees\\[(\\d+)\\]/);
                const attendeeIndex = match ? parseInt(match[1], 10) : 0;
                const attendee = plan.attendees[attendeeIndex] || plan.attendees[0] || {{}};
                const label = labelOf(el);
                let applied = false;
                let usedValue = '';

                if (el.type === 'radio' && label.includes('我理解並同意')) {{
                  usedValue = attendee.agree_real_name ? 'agree' : '';
                  applied = attendee.agree_real_name ? setRadio(el) : false;
                }} else if (label.includes('姓名')) {{
                  usedValue = attendee.name || plan.contact.name;
                  applied = setText(el, usedValue);
                }} else if (label.includes('手機')) {{
                  usedValue = attendee.phone || plan.contact.phone;
                  applied = setText(el, usedValue);
                }} else if (label.includes('身分證') || name.includes('[field_idnumber_')) {{
                  usedValue = attendee.id_number;
                  applied = setText(el, usedValue);
                }}

                if (applied) {{
                  pushApplied(name, label, usedValue);
                }} else if (requiredOf(el)) {{
                  pushMissing(name, label);
                }}
              }}

              const extraCheckboxes = [...document.querySelectorAll('.additional-info label.checkbox-inline')];
              for (const labelEl of extraCheckboxes) {{
                const text = clean(labelEl.innerText);
                const box = labelEl.querySelector(\"input[type='checkbox']\");
                if (!box) continue;
                if (text.includes(\"Show that you've been to this event on public page.\")) {{
                  setCheckbox(box, !!plan.options.display_public_attendance);
                  pushApplied(box.name || '<display_public>', text, String(!!plan.options.display_public_attendance));
                }} else if (text.includes('To be a fan of')) {{
                  setCheckbox(box, !!plan.options.join_organizer_fan);
                  pushApplied(box.name || '<join_fan>', text, String(!!plan.options.join_organizer_fan));
                }}
              }}

              return result;
            }})()
            """
        )
        logger.info(
            "KKTIX order form autofill 完成: applied=%d missing_required=%d",
            len(result.get("applied", [])),
            len(result.get("missing_required", [])),
        )
        return result

    async def login(self) -> None:
        """
        Open the KKTIX login page for manual inspection or manual sign-in.

        TODO:
        - verify live sign-in DOM after Cloudflare challenge
        - determine whether browser-first login is sufficient for later API steps
        """
        await self._ensure_page()
        await self.page.goto(KKTIX_SIGN_IN_URL)
        logger.info("已開啟 KKTIX 登入頁: %s", KKTIX_SIGN_IN_URL)

    async def run(self) -> bool:
        try:
            await self._ensure_page()
            current_url = await self.page.current_url()
            if "/registrations/" in current_url and "/registrations/new" not in current_url:
                order_info = await self.inspect_order_page()
            else:
                registration_info = await self.inspect_registration_page()
                reserve_result = await self.reserve_tickets(registration_info)
                order_info = reserve_result["order_info"]

            autofill_result = None
            if self.config.kktix.enabled:
                autofill_result = await self.autofill_order_form()

            summary = [
                f"票種: {order_info.get('order_summary', {}).get('ticket_name', '')}",
                f"總額: {order_info.get('order_summary', {}).get('total_amount', '')}",
            ]
            if autofill_result is not None:
                summary.append(f"填入欄位: {len(autofill_result.get('applied', []))}")
                summary.append(f"缺少必填: {len(autofill_result.get('missing_required', []))}")
            self.last_success_info = "\n".join(item for item in summary if item and not item.endswith(": "))
            logger.info("KKTIX 已進入 reserved order 頁，停在 Confirm Form 前")
            return True
        except Exception:
            logger.exception("KKTIX run 流程失敗")
            return False

    async def watch(self, interval: float = 5.0) -> bool:
        await self._ensure_page()
        while True:
            try:
                registration_info = await self.inspect_registration_page()
                try:
                    build_registration_selection_plan(self.event, registration_info)
                except ValueError as exc:
                    logger.info("KKTIX watch: %s，%.1f 秒後重試", exc, interval)
                    await self.page.sleep(interval)
                    continue

                reserve_result = await self.reserve_tickets(registration_info)
                if self.config.kktix.enabled:
                    await self.autofill_order_form()
                self.last_success_info = (
                    f"票種: {reserve_result['order_info'].get('order_summary', {}).get('ticket_name', '')}\n"
                    f"總額: {reserve_result['order_info'].get('order_summary', {}).get('total_amount', '')}"
                ).strip()
                return True
            except Exception:
                logger.exception("KKTIX watch 迴圈失敗，%.1f 秒後重試", interval)
                await self.page.sleep(interval)

    async def cancel_order(self, timeout: float = 10.0) -> bool:
        await self._ensure_page()
        info = await self._parse_current_order_page()
        if not info.get("flags", {}).get("has_cancel_ticket"):
            return False

        clicked = await self.page.evaluate(
            """
            (() => {
              window.confirm = () => true;
              const button = document.querySelector("a.reselect-ticket");
              if (!button) return false;
              button.click();
              return true;
            })()
            """
        )
        if not clicked:
            return False

        elapsed = 0.0
        while elapsed < timeout:
            current_url = await self.page.current_url()
            if "/registrations/new" in current_url:
                return True
            await self.page.sleep(0.25)
            elapsed += 0.25
        return False

    async def close(self) -> None:
        await self.engine.close()
