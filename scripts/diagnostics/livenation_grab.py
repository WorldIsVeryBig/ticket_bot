"""LiveNation → tixcraft 跳轉搶票腳本

流程：
1. 開啟 LiveNation 活動頁面，等待購票按鈕出現
2. 偵測到按鈕後，提取 tixcraft URL 或點擊跳轉
3. 到達 tixcraft 後，平行啟動多個 API watch 搶票
"""

from __future__ import annotations

import asyncio
import logging
import os
import socket
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

from dotenv import load_dotenv

# 加入 src 到 path
sys.path.insert(0, str((Path(__file__).resolve().parents[2] / "src")))

from ticket_bot.config import load_config, EventConfig, SessionConfig
from ticket_bot.browser import create_engine
from ticket_bot.platforms.tixcraft_api import TixcraftApiBot

load_dotenv()

logger = logging.getLogger("livenation_grab")

# ── 設定 ──────────────────────────────────────────────────────
LIVENATION_URL = "https://www.livenation.com.tw/en/event/exo-planet-6-exhorizon-in-taipei-taipei-tickets-edp1662415"
SALE_HOUR = 11  # 11:00 AM
SALE_MINUTE = 0

TARGET_DATES = [
    {"label": "EXO 5/9", "date_keyword": "2026/05/09", "ticket_count": 1, "area_keyword": "800"},
]

CONFIG_PATH = "config.yaml"
WATCH_INTERVAL = 3.0

# ── 按鈕偵測 JS ──────────────────────────────────────────────
DETECT_BUTTON_JS = """
(() => {
    // 策略 0: 頁面已跳轉到 tixcraft
    if (window.location.hostname.includes('tixcraft.com')) {
        return { found: true, strategy: 'already_redirected', href: window.location.href, text: '' };
    }

    // 策略 1: 第一個 ticket-container（presale）裡找 tixcraft 連結或 Buy 按鈕
    const containers = document.querySelectorAll('.ticket-container');
    const presaleBox = containers[0];  // Live Nation Taiwan會員預售
    if (presaleBox) {
        // 1a: tixcraft 連結
        const tixLink = presaleBox.querySelector('a[href*="tixcraft.com"]');
        if (tixLink) {
            return { found: true, strategy: 'presale_tixcraft_link', href: tixLink.href, text: tixLink.textContent.trim() };
        }

        // 1b: "On sale soon" 消失了 → 找新出現的 <a> 或 <button>
        const onSaleSoon = presaleBox.querySelector('p.haia-1fdk2sy');
        const hasSoon = onSaleSoon && /on sale soon|即將開賣/i.test(onSaleSoon.textContent);
        if (!hasSoon) {
            // 找 presale box 裡任何可點的按鈕/連結
            const clickables = presaleBox.querySelectorAll('a[href], button:not([aria-label="Ticket information"])');
            for (const el of clickables) {
                const style = window.getComputedStyle(el);
                if (style.display === 'none' || style.visibility === 'hidden') continue;
                const text = el.textContent.trim();
                if (/info|資訊/i.test(text)) continue;
                return {
                    found: true, strategy: 'presale_new_button',
                    href: el.href || '', text: text.substring(0, 80),
                    tag: el.tagName, classes: (el.className || '').substring(0, 150)
                };
            }
        }
    }

    // 策略 2: 全頁面找 tixcraft 連結
    const tixLinks = document.querySelectorAll('a[href*="tixcraft.com"]');
    for (const link of tixLinks) {
        const style = window.getComputedStyle(link);
        if (style.display !== 'none' && style.visibility !== 'hidden') {
            return { found: true, strategy: 'tixcraft_link', href: link.href, text: link.textContent.trim() };
        }
    }

    // 策略 3: 找含購票文字的可見按鈕/連結
    const buyPatterns = /buy|ticket|購票|立即購票|get ticket|立即搶購/i;
    const skipPatterns = /on sale soon|即將開賣|register|註冊|sign up|info|資訊|ticket information/i;
    const allClickable = document.querySelectorAll('a, button, [role="button"]');
    for (const el of allClickable) {
        const text = el.textContent.trim();
        if (!buyPatterns.test(text)) continue;
        if (skipPatterns.test(text)) continue;
        const style = window.getComputedStyle(el);
        if (style.display === 'none' || style.visibility === 'hidden' ||
            style.opacity === '0' || el.disabled) continue;
        return {
            found: true, strategy: 'buy_button',
            href: el.href || el.getAttribute('data-href') || '',
            text: text.substring(0, 80),
            tag: el.tagName
        };
    }

    return { found: false };
})()
"""

# 點擊按鈕的 JS（優先點 presale container 裡的按鈕）
CLICK_BUTTON_JS = """
(() => {
    // 優先點 presale container 裡的按鈕
    const containers = document.querySelectorAll('.ticket-container');
    const presaleBox = containers[0];
    if (presaleBox) {
        const tixLink = presaleBox.querySelector('a[href*="tixcraft.com"]');
        if (tixLink) { tixLink.click(); return 'clicked_tixcraft_link'; }

        const clickables = presaleBox.querySelectorAll('a[href], button');
        for (const el of clickables) {
            const text = el.textContent.trim();
            if (/info|資訊|ticket information/i.test(text)) continue;
            if (/on sale soon|即將開賣/i.test(text)) continue;
            const style = window.getComputedStyle(el);
            if (style.display !== 'none' && !el.disabled) {
                el.click(); return 'clicked_presale_button: ' + text.substring(0, 50);
            }
        }
    }

    // fallback: 全頁面找
    const tixLinks = document.querySelectorAll('a[href*="tixcraft.com"]');
    if (tixLinks.length > 0) { tixLinks[0].click(); return 'clicked_global_tixcraft'; }

    const buyPatterns = /buy|ticket|購票|立即購票|get ticket/i;
    const all = document.querySelectorAll('a, button, [role="button"]');
    for (const el of all) {
        const text = el.textContent.trim();
        if (buyPatterns.test(text) && !/on sale soon|register/i.test(text)) {
            const style = window.getComputedStyle(el);
            if (style.display !== 'none' && !el.disabled) {
                el.click(); return 'clicked_buy: ' + text.substring(0, 50);
            }
        }
    }
    return false;
})()
"""


async def wait_for_sale_time():
    """等到開賣前 5 秒"""
    tz = timezone(timedelta(hours=8))
    now = datetime.now(tz)
    sale_time = now.replace(hour=SALE_HOUR, minute=SALE_MINUTE, second=0, microsecond=0)

    if now >= sale_time:
        logger.info("已過開賣時間，直接開始偵測")
        return

    wait_until = sale_time - timedelta(seconds=5)
    delta = (wait_until - now).total_seconds()

    if delta > 0:
        logger.info("等待至 %s（還有 %.0f 秒）...", sale_time.strftime("%H:%M:%S"), delta + 5)
        # 每 30 秒 log 一次
        while True:
            remaining = (wait_until - datetime.now(tz)).total_seconds()
            if remaining <= 0:
                break
            sleep_time = min(remaining, 30)
            await asyncio.sleep(sleep_time)
            remaining = (sale_time - datetime.now(tz)).total_seconds()
            if remaining > 5:
                logger.info("距開賣還有 %.0f 秒...", remaining)

    logger.info("進入快速輪詢模式！")


async def detect_and_redirect(page) -> str:
    """偵測 LiveNation 購票按鈕，返回 tixcraft game URL"""

    logger.info("開始偵測購票按鈕（每 100ms）...")
    poll_count = 0
    last_refresh = time.monotonic()

    while True:
        poll_count += 1
        try:
            result = await page.evaluate(DETECT_BUTTON_JS)
        except Exception as e:
            # 頁面跳轉中可能 context 斷裂
            logger.debug("evaluate 失敗（可能正在跳轉）: %s", e)
            await asyncio.sleep(0.2)
            # 檢查是否已經跳到 tixcraft
            try:
                url = await page.evaluate("window.location.href")
                if "tixcraft.com" in str(url):
                    logger.info("頁面已跳轉到 tixcraft: %s", url)
                    return _normalize_tixcraft_url(str(url))
            except Exception:
                pass
            continue

        if result and result.get("found"):
            strategy = result.get("strategy", "unknown")
            href = result.get("href", "")
            text = result.get("text", "")
            logger.info("偵測到購票按鈕！策略=%s, text=%s, href=%s", strategy, text, href[:100])

            # 已經在 tixcraft
            if strategy == "already_redirected":
                return _normalize_tixcraft_url(href)

            # href 直接含 tixcraft URL → 直接導航（最快）
            if href and "tixcraft.com" in href:
                logger.info("直接導航到 tixcraft: %s", href)
                await page.goto(href)
                await asyncio.sleep(0.5)
                url = await page.evaluate("window.location.href")
                return _normalize_tixcraft_url(str(url))

            # 無 tixcraft href → 點擊按鈕，等待跳轉
            logger.info("點擊按鈕，等待跳轉...")
            try:
                await page.evaluate(CLICK_BUTTON_JS)
            except Exception:
                pass

            # 等待跳轉到 tixcraft（最多 15 秒）
            for i in range(300):
                await asyncio.sleep(0.05)
                try:
                    url = await page.evaluate("window.location.href")
                    if "tixcraft.com" in str(url):
                        logger.info("成功跳轉到 tixcraft: %s", url)
                        return _normalize_tixcraft_url(str(url))
                except Exception:
                    continue

            logger.warning("點擊後 15 秒內未跳轉到 tixcraft，重新偵測...")

        # 每 30 秒重新整理頁面，避免狀態過期
        if time.monotonic() - last_refresh > 30:
            logger.info("[第 %d 次] 重新整理 LiveNation 頁面...", poll_count)
            try:
                await page.goto(LIVENATION_URL)
                await asyncio.sleep(1)
            except Exception:
                pass
            last_refresh = time.monotonic()

        if poll_count % 50 == 0:
            logger.info("[第 %d 次] 持續偵測中...", poll_count)

        await asyncio.sleep(0.1)


def _normalize_tixcraft_url(url: str) -> str:
    """將 tixcraft URL 標準化為 game URL"""
    clean_url = url.split("?")[0]
    if "/activity/detail/" in url:
        slug = clean_url.rstrip("/").split("/")[-1]
        return f"https://tixcraft.com/activity/game/{slug}"
    if "/activity/game/" in url:
        # 去除 query string
        return clean_url
    # 其他 tixcraft URL（area, ticket 等）→ 直接返回
    return clean_url


async def run_parallel_watch(cfg, game_url: str, engine, page):
    """平行啟動多個 API watch

    關鍵設計：第一個 bot 搶到票進入 checkout 後，鎖住瀏覽器讓使用者刷卡。
    第二個 bot 只用 API 搶票，不碰瀏覽器。
    """
    sess = cfg.sessions[0] if cfg.sessions else SessionConfig()

    # 瀏覽器鎖：第一個成功的 bot 鎖住，其他 bot 不能導航瀏覽器
    browser_lock = asyncio.Lock()
    browser_locked_by: list[str] = []  # 記錄哪個 bot 鎖住了瀏覽器
    grab_count: list[int] = [0]  # 搶到幾張（用 list 讓 closure 可寫）

    bots = []
    for target in TARGET_DATES:
        ev = EventConfig(
            name=target["label"],
            platform="tixcraft",
            url=game_url,
            ticket_count=target["ticket_count"],
            date_keyword=target["date_keyword"],
            area_keyword=target.get("area_keyword", ""),
        )
        bot = TixcraftApiBot(cfg, ev, session=sess)
        # 共用已啟動的 engine 和 page（避免重新開瀏覽器）
        bot.engine = engine
        bot.page = page
        bots.append((target["label"], bot))

    # 初始化所有 bot 的 HTTP session（共用瀏覽器 cookie）
    for label, bot in bots:
        await bot._wait_for_browser_session_ready()
        await bot._init_http()
        logger.info("[%s] API session 初始化完成", label)

    # 平行執行 watch（跳過 browser startup）
    async def _watch_single(label: str, bot: TixcraftApiBot):
        """單一 bot 的 API watch loop（不重新啟動瀏覽器）"""
        game = bot.event.url
        if "/activity/detail/" in game:
            slug = game.rstrip("/").split("/")[-1]
            game = f"https://tixcraft.com/activity/game/{slug}"

        area_url = await bot._navigate_to_area_api(game)
        retry = 0
        while area_url is None:
            retry += 1
            logger.warning("[%s] 無法進入區域頁（第 %d 次），%.1f 秒後重試...", label, retry, WATCH_INTERVAL)
            await asyncio.sleep(WATCH_INTERVAL)
            area_url = await bot._navigate_to_area_api(game)

        logger.info("[%s] 開始監測釋票 (區域: %s)", label, area_url)

        from ticket_bot.platforms.tixcraft_parser import parse_area_list
        from ticket_bot.platforms.tixcraft_api import LoginExpiredError, BASE
        import re

        skip_re = re.compile(r'身心障礙|身障|輪椅|wheelchair|殘障|站區|搖滾站', re.IGNORECASE)
        round_num = 0
        consecutive_errors = 0

        while True:
            round_num += 1
            try:
                await bot._ensure_session()
                resp = await bot._api_get(area_url)
                consecutive_errors = 0

                if resp.status_code in (301, 302):
                    loc = bot._absolute_url(resp.headers.get("Location", ""))
                    if "/ticket/area/" in loc:
                        area_url = loc
                    else:
                        new_area = await bot._navigate_to_area_api(game)
                        if new_area:
                            area_url = new_area
                    await asyncio.sleep(WATCH_INTERVAL)
                    continue

                html = resp.text
                area_info = parse_area_list(html)
                all_available = area_info["available"]
                available = [a for a in all_available if not skip_re.search(a["text"])]
                disabled_only = [a for a in all_available if skip_re.search(a["text"])]

                if not available and disabled_only:
                    if round_num % 10 == 1:
                        logger.info("[%s][第 %d 輪] 只剩身障票 (%d 區)", label, round_num, len(disabled_only))
                    await asyncio.sleep(WATCH_INTERVAL)
                    continue

                if available:
                    logger.info("[%s] 偵測到 %d 個可用區域！", label, len(available))
                    target = available[0]
                    area_text = target["text"][:60]
                    href = target["href"]
                    if href.startswith("/"):
                        href = f"{BASE}{href}"

                    # 記錄選中的區域
                    bot._selected_area_text = area_text

                    # 搶票前：如果瀏覽器已被其他 bot 鎖住，禁止 bot 碰瀏覽器
                    if browser_locked_by and browser_locked_by[0] != label:
                        # 暫時移除 page 引用，讓 API bot 不碰瀏覽器
                        saved_page = bot.page
                        bot.page = None
                        logger.info("[%s] 瀏覽器已被 %s 佔用，僅用 API 搶票", label, browser_locked_by[0])

                    success = await bot._fill_ticket_form_api(href)

                    # 還原 page 引用
                    if bot.page is None:
                        bot.page = saved_page

                    if success:
                        grab_count[0] += 1
                        ticket_info = f"場次: {label}\n區域: {area_text}\n張數: {bot.event.ticket_count}"
                        logger.info("[%s] 第 %d 張搶票成功！\n%s", label, grab_count[0], ticket_info)

                        # 第 1 張：鎖住當前頁面給使用者刷卡
                        # 第 2 張+：開新分頁處理 checkout
                        if not browser_locked_by:
                            async with browser_lock:
                                browser_locked_by.append(label)
                            logger.info("[%s] 💳 已鎖定瀏覽器，等待刷卡付款...", label)
                        else:
                            # 開新分頁處理 checkout
                            logger.info("[%s] 開新分頁處理第 %d 張 checkout...", label, grab_count[0])
                            try:
                                new_page = await engine.new_page()
                                # 同步 cookie 到新分頁
                                cookies = [{"name": k, "value": v, "url": "https://tixcraft.com"}
                                           for k, v in bot._http.cookies.items()]
                                if cookies:
                                    await new_page.set_cookies(cookies)
                                await new_page.goto("https://tixcraft.com/ticket/order")
                                logger.info("[%s] 💳 新分頁已開啟 /ticket/order，請完成刷卡付款", label)
                            except Exception as e:
                                logger.warning("[%s] 開新分頁失敗: %s，請手動到 tixcraft 票夾付款", label, e)

                        # 發送通知
                        try:
                            tg_token = os.getenv("TELEGRAM_BOT_TOKEN", "")
                            tg_chat = os.getenv("TELEGRAM_CHAT_ID", "")
                            if tg_token and tg_chat:
                                import httpx
                                async with httpx.AsyncClient() as client:
                                    await client.post(
                                        f"https://api.telegram.org/bot{tg_token}/sendMessage",
                                        json={"chat_id": tg_chat,
                                              "text": f"🎉 第 {grab_count[0]} 張搶到！\n{label}\n區域: {area_text}\n張數: {bot.event.ticket_count}\n請在瀏覽器完成付款。",
                                              "parse_mode": "HTML"}
                                    )
                        except Exception:
                            pass

                        # 切純 API 繼續搶下一張
                        if grab_count[0] == 1:
                            bot.page = None
                        logger.info("[%s] 繼續搶第 %d 張...", label, grab_count[0] + 1)
                        try:
                            await bot._refresh_session()
                        except Exception:
                            pass
                        continue  # 不 return，繼續搶
                    else:
                        logger.warning("[%s] 結帳失敗: %s，繼續監測...", label, bot.last_error)
                else:
                    if round_num % 20 == 1:
                        logger.info("[%s][第 %d 輪] 尚無可用票券", label, round_num)

            except LoginExpiredError:
                logger.error("[%s] 登入過期！", label)
                return False
            except Exception:
                consecutive_errors += 1
                if consecutive_errors <= 3:
                    logger.exception("[%s][第 %d 輪] watch 錯誤", label, round_num)
                if consecutive_errors == 5:
                    logger.error("[%s] 連續 5 次錯誤，嘗試刷新 session...", label)
                    if await bot._refresh_session():
                        consecutive_errors = 0
                    else:
                        logger.error("[%s] session 恢復失敗", label)
                        return False

            await asyncio.sleep(WATCH_INTERVAL)

    tasks = [asyncio.create_task(_watch_single(label, bot)) for label, bot in bots]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    for (label, _), result in zip(bots, results):
        if isinstance(result, Exception):
            logger.error("[%s] 異常結束: %s", label, result)
        elif result:
            logger.info("[%s] 搶票成功！", label)
        else:
            logger.info("[%s] 搶票結束（未成功）", label)


async def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    # 減少 nodriver 噪音
    logging.getLogger("nodriver").setLevel(logging.WARNING)
    logging.getLogger("uc").setLevel(logging.WARNING)

    cfg = load_config(CONFIG_PATH)
    sess = cfg.sessions[0] if cfg.sessions else SessionConfig()

    logger.info("=== LiveNation → tixcraft 搶票腳本 ===")
    logger.info("目標: %s", ", ".join(t["label"] for t in TARGET_DATES))
    logger.info("開賣時間: %02d:%02d", SALE_HOUR, SALE_MINUTE)

    # 1. 啟動瀏覽器
    engine = create_engine(cfg.browser.engine)
    user_data_dir = sess.user_data_dir if sess else cfg.browser.user_data_dir
    await engine.launch(
        headless=cfg.browser.headless,
        user_data_dir=user_data_dir,
        executable_path=cfg.browser.executable_path,
        lang=cfg.browser.lang,
    )
    logger.info("瀏覽器啟動完成")

    # 2. 開啟 LiveNation 頁面
    page = await engine.new_page()
    await page.goto(LIVENATION_URL)
    logger.info("LiveNation 頁面載入完成")

    # 3. 背景 DNS prefetch tixcraft.com
    asyncio.get_event_loop().run_in_executor(
        None, lambda: socket.getaddrinfo("tixcraft.com", 443)
    )

    # 4. 等待開賣時間
    await wait_for_sale_time()

    # 5. 偵測按鈕 + 跳轉
    tixcraft_url = await detect_and_redirect(page)
    logger.info("=== 取得 tixcraft URL: %s ===", tixcraft_url)

    # 6. 確保在 tixcraft 頁面（讓瀏覽器拿到 cookie）
    current = await page.evaluate("window.location.href")
    if "tixcraft.com" not in str(current):
        await page.goto(tixcraft_url)
        await asyncio.sleep(1)

    # 7. 平行 watch 搶票
    logger.info("=== 啟動平行 API watch ===")
    await run_parallel_watch(cfg, tixcraft_url, engine, page)

    # 保持瀏覽器開啟（付款用）
    logger.info("搶票流程結束，瀏覽器保持開啟。按 Ctrl+C 退出。")
    try:
        while True:
            await asyncio.sleep(60)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    asyncio.run(main())
