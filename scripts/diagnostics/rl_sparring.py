"""RL 實戰練習腳本 — 用真實 tixcraft 環境收集 reward 訓練三個 RL 模組

支援兩種模式：
  --mode captcha  (預設) 直接打 ticket 頁面練 captcha，售完頁面也能練
  --mode full     完整流程：掃描未售完活動 → game → area → ticket → captcha → 提交
                  ⚠ 提交後不跟轉付款頁（follow_redirects=False），不會真的買票

原理：
  tixcraft 即使票已售完，captcha 仍然會先驗證。
  驗證碼正確 → 回傳 302 redirect（付款頁）或「售完」訊息
  驗證碼錯誤 → 回傳「驗證碼錯誤」help-block
  → 這就是 bandit 的 reward signal！

  full 模式額外收集：
  - game 頁面回應速度 → retry Q-learner
  - area 頁面競爭狀態 → burst bandit context
  - 完整流程的端到端延遲

Usage:
    # 快速模式：只練 captcha（預設）
    .venv/bin/python scripts/diagnostics/rl_sparring.py --rounds 100

    # 完整流程測試：掃描未售完場次走完 game→area→ticket→captcha
    .venv/bin/python scripts/diagnostics/rl_sparring.py --mode full --rounds 50

    # 指定頁面
    .venv/bin/python scripts/diagnostics/rl_sparring.py --page "https://tixcraft.com/ticket/ticket/..."
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import random
import re
import socket
import sys
import time
from datetime import datetime
from pathlib import Path

from curl_cffi import requests as curl_requests

# 加入 src 到 path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ticket_bot.config import CaptchaConfig
from ticket_bot.captcha.solver import CaptchaSolver
from ticket_bot.rl.bandit import ThresholdBandit
from ticket_bot.rl.burst_bandit import BurstBandit
from ticket_bot.rl.adaptive_retry import AdaptiveRetry
from ticket_bot.platforms.tixcraft_parser import (
    parse_game_list,
    parse_area_list,
    parse_ticket_form,
    detect_coming_soon,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("rl_sparring")

BASE = "https://tixcraft.com"

KNOWN_SLUGS = [
    "26_softbankh", "26_fujirock", "26_tpe4811th", "26_lany",
    "26_ive", "26_exokh", "26_itzy", "26_bus", "26_anson",
    "26_treasure", "26_megaport", "26_monstax", "26_gem", "25_lioneers",
]

FALLBACK_TICKET_PAGES = [
    "https://tixcraft.com/ticket/ticket/26_softbankh/21708/7/68",
]

CAPTCHA_WRONG_PATTERNS = re.compile(
    r"驗證碼|verify|captcha|verification", re.IGNORECASE,
)

SOLD_OUT_PATTERNS = re.compile(
    r"選購一空|已售完|sold out|no tickets|完売|暫無|此區已無票", re.IGNORECASE,
)


# ═══════════════════════════════════════════════════════════
#  工具
# ═══════════════════════════════════════════════════════════

def measure_latency(host: str = "tixcraft.com", port: int = 443, times: int = 5) -> float:
    """測量到 tixcraft 的 TCP 延遲 (ms)，取中位數"""
    results = []
    for _ in range(times):
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(5)
            start = time.perf_counter()
            sock.connect((host, port))
            latency = (time.perf_counter() - start) * 1000
            sock.close()
            results.append(latency)
        except Exception:
            results.append(999.0)
    return sorted(results)[len(results) // 2]


# ═══════════════════════════════════════════════════════════
#  Full Flow：用 parser 走完 game → area → ticket（純 HTTP）
# ═══════════════════════════════════════════════════════════

async def scan_available_events_http(
    client: curl_requests.AsyncSession,
) -> list[dict]:
    """用 HTTP 掃描所有活動，回傳有未售完場次的 {slug, game_href, game_text} 列表"""
    results = []

    for slug in KNOWN_SLUGS:
        try:
            resp = await client.get(
                f"{BASE}/activity/game/{slug}", allow_redirects=True,
            )
            if resp.status_code != 200:
                continue

            if detect_coming_soon(resp.text):
                logger.info("  %s: 即將開賣（跳過）", slug)
                continue

            info = parse_game_list(resp.text)
            available = info["available"]
            sold = len(info["sold_out"])

            if available:
                for g in available[:3]:
                    results.append({
                        "slug": slug,
                        "game_href": g["href"],
                        "game_text": g["text"],
                    })
                logger.info(
                    "  ✓ %s: %d 可用 / %d 售完 → %s",
                    slug, len(available), sold, available[0]["text"][:30],
                )
            else:
                logger.info("  ✗ %s: %d 場全售完", slug, sold)

        except Exception as e:
            logger.debug("  %s: %s", slug, e)
        await asyncio.sleep(1.0)

    return results


async def navigate_to_ticket_http(
    client: curl_requests.AsyncSession,
    game_href: str,
    adaptive: AdaptiveRetry,
) -> tuple[str | None, dict]:
    """HTTP 走完 game_href → area → ticket，回傳 (ticket_url, step_latencies)

    同時餵 adaptive retry Q-learner。
    """
    latencies = {"game_to_area": 0.0, "area_to_ticket": 0.0}
    adaptive.start_episode()

    # ── Step 1: area 頁面 ──
    area_url = game_href if game_href.startswith("http") else f"{BASE}{game_href}"
    t0 = time.perf_counter()
    try:
        resp = await client.get(area_url, allow_redirects=True)
    except Exception as e:
        adaptive.update(success=False, error=e)
        return None, latencies
    latencies["game_to_area"] = (time.perf_counter() - t0) * 1000

    if resp.status_code != 200:
        adaptive.update(success=False, status_code=resp.status_code)
        return None, latencies

    adaptive.update(success=True, status_code=resp.status_code)

    # 解析 area 頁面
    area_info = parse_area_list(resp.text)
    available_areas = area_info["available"]
    if not available_areas:
        # 嘗試從 JS areaUrlList 拿
        m = re.search(r"areaUrlList\s*=\s*(\{[^}]+\})", resp.text)
        if m:
            try:
                url_map = json.loads(m.group(1).replace("'", '"'))
                for url in url_map.values():
                    if "/ticket/ticket/" in url:
                        return (url if url.startswith("http") else f"{BASE}{url}"), latencies
            except Exception:
                pass
        logger.debug("  area 頁面無可用區域")
        return None, latencies

    # 隨機選一個可用區域（不總是選第一個，增加多樣性）
    area = random.choice(available_areas)
    area_href = area["href"]
    if not area_href:
        return None, latencies

    # ── Step 2: ticket 頁面 ──
    ticket_area_url = area_href if area_href.startswith("http") else f"{BASE}{area_href}"
    t1 = time.perf_counter()
    try:
        resp2 = await client.get(ticket_area_url, allow_redirects=True)
    except Exception as e:
        adaptive.update(success=False, error=e)
        return None, latencies
    latencies["area_to_ticket"] = (time.perf_counter() - t1) * 1000

    # 有些 area href 直接就是 /ticket/ticket/... URL
    final_url = str(resp2.url)
    if "/ticket/ticket/" in final_url:
        adaptive.update(success=True, status_code=resp2.status_code)
        return final_url, latencies

    # area 頁面裡有 areaUrlList → 拿 ticket URL
    m = re.search(r"areaUrlList\s*=\s*(\{[^}]+\})", resp2.text)
    if m:
        try:
            url_map = json.loads(m.group(1).replace("'", '"'))
            for url in url_map.values():
                if "/ticket/ticket/" in url:
                    full = url if url.startswith("http") else f"{BASE}{url}"
                    adaptive.update(success=True, status_code=200)
                    return full, latencies
        except Exception:
            pass

    adaptive.update(success=False, status_code=resp2.status_code)
    return None, latencies


# ═══════════════════════════════════════════════════════════
#  Captcha Sparring（單次回合）
# ═══════════════════════════════════════════════════════════

async def bot_sparring_round(
    event_config: "EventConfig",
    app_config: "AppConfig",
    bandit: ThresholdBandit,
) -> dict:
    """用真正的 TixcraftBot 跑完整購票流程

    bot.run() 會自動處理：
      game 選擇 → verify → area 選擇 → 勾同意 → 選票數 →
      辨識 captcha → 填入 → 點送出 → 處理 JS alert → 跳轉付款頁
    """
    from ticket_bot.platforms.tixcraft import TixcraftBot

    result = {
        "threshold": 0.0, "submitted": True,
        "captcha_correct": None, "flow_success": False,
        "latency_ms": 0.0, "error_msg": "",
    }

    # bandit 選 threshold，覆寫到 config
    threshold = bandit.select()
    result["threshold"] = threshold
    app_config.captcha.confidence_threshold = threshold

    bot = TixcraftBot(app_config, event_config)
    t0 = time.perf_counter()

    try:
        # 限時 60 秒，避免售完場次無限循環
        success = await asyncio.wait_for(bot.run(), timeout=60.0)
        result["latency_ms"] = (time.perf_counter() - t0) * 1000
        result["flow_success"] = success

        if success:
            result["captcha_correct"] = True
            logger.info("    ✓ 搶票成功！已進入付款頁 (%.0fms)", result["latency_ms"])
        else:
            result["captcha_correct"] = False
            logger.info("    ✗ 流程未完成 (%.0fms)", result["latency_ms"])
    except asyncio.TimeoutError:
        result["latency_ms"] = (time.perf_counter() - t0) * 1000
        result["error_msg"] = "timeout 60s"
        logger.info("    ⏱ 超時 60s（可能區域全售完循環）")
    except Exception as e:
        result["latency_ms"] = (time.perf_counter() - t0) * 1000
        result["error_msg"] = str(e)
        logger.warning("    ✗ 例外: %s", e)
    finally:
        try:
            await bot.close()
        except Exception:
            pass

    return result


# ═══════════════════════════════════════════════════════════
#  Retry Probing
# ═══════════════════════════════════════════════════════════

async def retry_probing(
    client: curl_requests.AsyncSession,
    adaptive: AdaptiveRetry,
    page_url: str,
    rounds: int = 20,
):
    """模擬不同重試間隔，收集 server 回應模式"""
    logger.info("\n--- Retry Q-Learning 資料收集 (%d rounds) ---", rounds)
    adaptive.start_episode()

    for i in range(rounds):
        if not adaptive.should_retry:
            adaptive.start_episode()

        wait_time = adaptive.get_wait_time(status_code=503)
        await asyncio.sleep(wait_time)

        try:
            t0 = time.perf_counter()
            resp = await client.get(
                f"{BASE}/ticket/captcha",
                headers={"Referer": page_url},
                params={"v": str(random.random())},
            )
            latency = (time.perf_counter() - t0) * 1000
            status = resp.status_code
            success = status == 200 and len(resp.content) > 100
            adaptive.update(success=success, status_code=status)

            if (i + 1) % 5 == 0:
                logger.info(
                    "  Retry probe %d/%d: wait=%.1fs status=%d latency=%.0fms",
                    i + 1, rounds, wait_time, status, latency,
                )
        except Exception as e:
            adaptive.update(success=False, error=e)

    logger.info("  Q-table 更新完成")


# ═══════════════════════════════════════════════════════════
#  Browser 掃描（captcha 模式用，跟之前一樣）
# ═══════════════════════════════════════════════════════════

async def scan_ticket_pages_browser(browser) -> list[str]:
    """用瀏覽器掃描有 captcha 的 ticket URL"""
    ticket_urls = []

    for slug in KNOWN_SLUGS:
        try:
            page = await browser.get(f"{BASE}/activity/game/{slug}")
            await page.sleep(3)

            area_str = await page.evaluate("""
                (() => {
                    const ids = new Set();
                    document.querySelectorAll(
                        'a[href*="/ticket/area/"], button[data-href*="/ticket/area/"]'
                    ).forEach(el => {
                        const href = el.href || el.dataset.href || '';
                        const m = href.match(/\\/ticket\\/area\\/[^/]+\\/(\\d+)/);
                        if (m) ids.add(m[1]);
                    });
                    return Array.from(ids).join(',');
                })()
            """)

            if not area_str:
                continue

            area_ids = area_str.split(",")[:3]
            logger.info("  %s: %d 場次", slug, len(area_ids))

            for area_id in area_ids:
                try:
                    page = await browser.get(f"{BASE}/ticket/area/{slug}/{area_id}")
                    await page.sleep(3)
                    urls_str = await page.evaluate("""
                        (() => {
                            if (typeof areaUrlList !== 'undefined')
                                return Object.values(areaUrlList).join(',');
                            return '';
                        })()
                    """)
                    if urls_str:
                        for url in urls_str.split(","):
                            if "/ticket/ticket/" in url:
                                ticket_urls.append(url)
                except Exception:
                    continue
                await page.sleep(1)
        except Exception:
            continue

    random.shuffle(ticket_urls)

    # 篩選有 captcha 的頁面
    working = []
    for url in ticket_urls[:30]:
        try:
            page = await browser.get(url)
            await page.sleep(2)
            has_captcha = await page.evaluate(
                "document.querySelector('img[src*=\"captcha\"]') !== null"
            )
            if has_captcha:
                working.append(url)
                if len(working) >= 10:
                    break
        except Exception:
            continue

    return working


# ═══════════════════════════════════════════════════════════
#  主流程
# ═══════════════════════════════════════════════════════════

async def main(mode: str, rounds: int, page_url: str):
    import nodriver as uc

    print("=" * 60)
    print(f"  RL 實戰練習 — {mode.upper()} mode")
    print(f"  目標: {rounds} 回合")
    print("=" * 60)

    # ── 初始化 RL 模組 ──
    bandit = ThresholdBandit()
    burst_bandit = BurstBandit()
    adaptive = AdaptiveRetry()
    captcha_cfg = CaptchaConfig(
        beta_model=True, preprocess=True,
        custom_model_path="model/captcha_model.onnx",
        custom_charset_path="model/meta.json",
    )
    solver = CaptchaSolver(captcha_cfg)

    # ── 啟動瀏覽器 & 取 cookies ──
    print("\n[1] 啟動瀏覽器...")
    browser = await uc.start(
        headless=False,
        user_data_dir="./chrome_profile",
        lang="zh-TW",
        browser_executable_path=os.getenv("BROWSER_EXECUTABLE_PATH", ""),
    )

    # 造訪首頁取得登入態 cookies（含 HttpOnly: PHPSESSID, cf_clearance）
    page = await browser.get(f"{BASE}/")
    await page.sleep(3)
    ua = await page.evaluate("navigator.userAgent")

    # CDP 取全部 cookies（包含 HttpOnly）— 跟真實 bot 一樣
    import nodriver.cdp.network as cdp_net
    raw_cookies = await page.send(cdp_net.get_all_cookies())
    cookies = {}
    for c in raw_cookies:
        cookies[c.name] = c.value
    print(f"  Cookies: {len(cookies)} 個 (含 HttpOnly)")
    # 列出關鍵 cookies 確認
    for key in ["PHPSESSID", "cf_clearance", "SID", "TIXCRAFT_UID"]:
        if key in cookies:
            print(f"    ✓ {key}: {cookies[key][:12]}...")

    # ── 延遲測量 ──
    latency = measure_latency()
    print(f"  延遲: {latency:.0f}ms")
    burst_name, burst_offsets = burst_bandit.select(latency)
    print(f"  Burst: {burst_name} {burst_offsets}")

    stats = {
        "total": 0, "submitted": 0, "captcha_correct": 0,
        "captcha_wrong": 0, "skipped": 0,
        "full_flow_success": 0, "full_flow_no_ticket": 0,
        "redirects": [],
        "avg_latency": [], "flow_latencies": [],
    }

    # 用 curl_cffi 建立 session（impersonate Chrome，跟真實 bot 一致）
    client = curl_requests.AsyncSession(
        impersonate="chrome124",
        cookies=cookies,
        headers={
            "User-Agent": ua,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
            "Accept-Language": "zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7",
            "Origin": BASE,
            "Connection": "keep-alive",
            "Sec-Ch-Ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
            "Sec-Ch-Ua-Mobile": "?0",
            "Sec-Ch-Ua-Platform": '"macOS"',
        },
        allow_redirects=False,
        timeout=15,
    )

    async with client:

        if mode == "full":
            await _run_full_mode(
                client, browser, solver, bandit, burst_bandit, adaptive,
                rounds, page_url, stats,
            )
        else:
            await _run_captcha_mode(
                client, browser, solver, bandit, burst_bandit, adaptive,
                rounds, page_url, stats,
            )

        # ── Retry probing（兩個模式都做）──
        probe_url = FALLBACK_TICKET_PAGES[0]
        await retry_probing(client, adaptive, probe_url, rounds=min(rounds, 30))

    _print_final_report(stats, bandit, burst_bandit, adaptive, mode)
    browser.stop()


# ── Full Flow Mode ──

async def _run_full_mode(
    client, browser, solver, bandit, burst_bandit, adaptive,
    rounds, page_url, stats,
):
    """完整流程：用真正的 TixcraftBot 跑 game→area→ticket→captcha→checkout"""
    from ticket_bot.config import AppConfig, BrowserConfig, CaptchaConfig, EventConfig

    # 掃描未售完活動
    if page_url:
        # 從 URL 推 slug（如 /ticket/ticket/26_softbankh/... → 26_softbankh）
        slug = "custom"
        for part in page_url.split("/"):
            if part.startswith(("25_", "26_", "27_")):
                slug = part
                break
        events_info = [{"slug": slug, "game_href": page_url, "game_text": page_url[-40:]}]
    else:
        print("\n[2] 掃描未售完活動（HTTP）...")
        events_info = await scan_available_events_http(client)

    if not events_info:
        print("  找不到未售完活動")
        return

    print(f"  可用活動: {len(events_info)} 個\n")

    # ⚠ 關閉 sparring 的瀏覽器，讓 TixcraftBot 能用同一個 chrome_profile
    try:
        browser.stop()
        await asyncio.sleep(2)
    except Exception:
        pass

    print(f"[3] 開始 FULL FLOW 練習 ({rounds} 回合，使用真實 TixcraftBot)...\n")

    # 建立共用的 AppConfig（瀏覽器模式，不用 API）
    app_config = AppConfig(
        browser=BrowserConfig(
            engine="nodriver",
            headless=False,
            user_data_dir="./chrome_profile",
            pre_warm=True,
            lang="zh-TW",
            executable_path=os.getenv("BROWSER_EXECUTABLE_PATH", ""),
            api_mode="off",
        ),
        captcha=CaptchaConfig(
            beta_model=True,
            preprocess=True,
            custom_model_path="model/captcha_model.onnx",
            custom_charset_path="model/meta.json",
            confidence_threshold=0.6,
            max_attempts=3,
        ),
    )

    event_idx = 0
    for i in range(rounds):
        ev = events_info[event_idx % len(events_info)]
        event_idx += 1

        slug = ev["slug"]
        raw_url = ev["game_href"]

        # 判斷 URL 類型：ticket 頁面直接用，否則用 game URL
        if "/ticket/ticket/" in raw_url or "/ticket/area/" in raw_url:
            event_url = raw_url if raw_url.startswith("http") else f"{BASE}{raw_url}"
        elif "/activity/game/" in raw_url:
            event_url = raw_url if raw_url.startswith("http") else f"{BASE}{raw_url}"
        else:
            event_url = f"{BASE}/activity/game/{slug}"

        logger.info(
            "  [%d/%d] %s → %s",
            i + 1, rounds, slug, ev["game_text"][:30],
        )

        # 建立 EventConfig
        event_config = EventConfig(
            name=f"sparring_{slug}",
            platform="tixcraft",
            url=event_url,
            ticket_count=1,
        )

        # 跑完整流程
        result = await bot_sparring_round(event_config, app_config, bandit)
        stats["total"] += 1
        stats["submitted"] += 1

        if result["latency_ms"] > 0:
            stats["avg_latency"].append(result["latency_ms"])

        if result["flow_success"]:
            stats["full_flow_success"] += 1
            stats["captcha_correct"] += 1
            bandit.update(success=True)
            burst_bandit.update(success=True)
        elif result["captcha_correct"] is False:
            stats["captcha_wrong"] += 1
            bandit.update(success=False)
            burst_bandit.update(success=False)
        else:
            stats["full_flow_no_ticket"] += 1

        if (i + 1) % 3 == 0:
            _print_progress(i + 1, rounds, stats, mode="full")

        await asyncio.sleep(random.uniform(3.0, 6.0))


# ── Captcha-Only Mode ──

async def _run_captcha_mode(
    client, browser, solver, bandit, burst_bandit, adaptive,
    rounds, page_url, stats,
):
    """只練 captcha：直接打 ticket 頁面"""

    if page_url:
        pages = [page_url]
    else:
        print("\n[2] 掃描有 captcha 的 ticket 頁面...")
        pages = await scan_ticket_pages_browser(browser)
        if not pages:
            print("  使用 fallback")
            pages = FALLBACK_TICKET_PAGES

    print(f"  可用頁面: {len(pages)} 個\n")
    print(f"[3] 開始 captcha 對練 ({rounds} 回合)...\n")

    page_idx = 0
    for i in range(rounds):
        current_page = pages[page_idx % len(pages)]
        page_idx += 1

        result = await captcha_sparring_round(browser, client, solver, bandit, current_page)
        stats["total"] += 1

        if result["latency_ms"] > 0:
            stats["avg_latency"].append(result["latency_ms"])

        if not result["submitted"]:
            stats["skipped"] += 1
        else:
            stats["submitted"] += 1
            if result["captcha_correct"] is True:
                stats["captcha_correct"] += 1
                bandit.update(success=True)
                burst_bandit.update(success=True)
            elif result["captcha_correct"] is False:
                stats["captcha_wrong"] += 1
                bandit.update(success=False)
                burst_bandit.update(success=False)

        if (i + 1) % 10 == 0:
            _print_progress(i + 1, rounds, stats, mode="captcha")

        await asyncio.sleep(random.uniform(2.0, 4.0))


# ═══════════════════════════════════════════════════════════
#  輸出
# ═══════════════════════════════════════════════════════════

def _print_progress(current: int, total: int, stats: dict, mode: str = "captcha"):
    submitted = stats["submitted"]
    correct = stats["captcha_correct"]
    wrong = stats["captcha_wrong"]
    rate = correct / submitted * 100 if submitted > 0 else 0
    avg_lat = sum(stats["avg_latency"][-20:]) / max(len(stats["avg_latency"][-20:]), 1)

    extra = ""
    if mode == "full":
        ok = stats["full_flow_success"]
        fail = stats["full_flow_no_ticket"]
        extra = f" flow_ok:{ok} no_ticket:{fail}"

    logger.info(
        "  [%d/%d] 提交:%d 正確:%d 錯誤:%d (%.1f%%) 跳過:%d 延遲:%.0fms%s",
        current, total, submitted, correct, wrong, rate, stats["skipped"], avg_lat, extra,
    )


def _print_final_report(stats, bandit, burst_bandit, adaptive, mode):
    print("\n" + "=" * 60)
    print(f"  RL 實戰練習完成！({mode.upper()} mode)")
    print("=" * 60)

    submitted = stats["submitted"]
    correct = stats["captcha_correct"]
    wrong = stats["captcha_wrong"]

    print(f"\n  總回合:    {stats['total']}")
    print(f"  已提交:    {submitted}")
    if submitted:
        print(f"  正確:      {correct} ({correct/submitted*100:.1f}%)")
    else:
        print(f"  正確:      0")
    print(f"  錯誤:      {wrong}")
    print(f"  跳過:      {stats['skipped']}")

    if mode == "full":
        print(f"  完整 flow:  {stats['full_flow_success']} 次 302 redirect")
        print(f"  flow 失敗: {stats['full_flow_no_ticket']} 次（area 已售完）")
        if stats["flow_latencies"]:
            avg_flow = sum(stats["flow_latencies"]) / len(stats["flow_latencies"])
            print(f"  平均 flow 延遲: {avg_flow:.0f}ms (game→area→ticket)")
        if stats["redirects"]:
            print(f"  Redirect 範例:")
            for r in stats["redirects"][:3]:
                print(f"    → {r[:70]}")

    if stats["avg_latency"]:
        print(f"  平均 captcha 延遲: {sum(stats['avg_latency'])/len(stats['avg_latency']):.0f}ms")

    print("\n--- Captcha Bandit 統計 ---")
    for arm, s in sorted(bandit.stats().items()):
        bar = "█" * int(s["mean"] * 20) + "░" * (20 - int(s["mean"] * 20))
        print(f"  θ={arm:.1f}  {bar}  mean={s['mean']:.3f}  trials={s['trials']}")

    print("\n--- Burst Bandit (top patterns per bucket) ---")
    burst_stats = burst_bandit.stats()
    for bucket in ["ultra_low", "low", "medium", "high"]:
        if bucket not in burst_stats:
            continue
        best = max(burst_stats[bucket].items(), key=lambda x: x[1]["mean"])
        print(f"  {bucket:>10}: best={best[0]} (mean={best[1]['mean']:.3f}, trials={best[1]['trials']})")

    print("\n--- Retry Q-table (learned states) ---")
    q_stats = adaptive.stats()
    if q_stats:
        for state, info in list(q_stats.items())[:10]:
            print(f"  {state}: → {info['best_action']} (Q={info['q_value']:.3f})")
    else:
        print("  （尚無學習資料）")

    print(f"\n  持久化:")
    print(f"    data/rl/captcha_bandit.json")
    print(f"    data/rl/burst_bandit.json")
    print(f"    data/rl/retry_qtable.json")
    print()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="RL 實戰練習 — 用真實 tixcraft 訓練 RL 模組")
    parser.add_argument("--mode", "-m", default="captcha", choices=["captcha", "full"],
                        help="captcha=只練驗證碼, full=完整流程（掃描未售完場次）")
    parser.add_argument("--rounds", "-n", type=int, default=100, help="練習回合數")
    parser.add_argument("--page", default="", help="指定票務頁面 URL（跳過掃描）")
    args = parser.parse_args()
    asyncio.run(main(args.mode, args.rounds, args.page))
