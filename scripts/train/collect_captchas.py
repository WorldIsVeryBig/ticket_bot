"""批量收集 tixcraft 驗證碼圖片供訓練用

使用方式：
    .venv/bin/python scripts/train/collect_captchas.py --count 200

會自動：
1. 用瀏覽器登入 session 進入票務頁面
2. 反覆呼叫 captcha API 抓取驗證碼圖片
3. 儲存到 captcha_samples/ 目錄
"""

import argparse
import asyncio
import sys
from datetime import datetime
from pathlib import Path

import httpx
import nodriver as uc


async def collect(count: int, output_dir: str, event_url: str, executable_path: str):
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)

    print("啟動瀏覽器...")
    kwargs = dict(
        headless=False,
        user_data_dir="./chrome_profile",
        lang="zh-TW",
    )
    if executable_path:
        kwargs["browser_executable_path"] = executable_path

    browser = await uc.start(**kwargs)

    # 進入場次頁
    print(f"載入活動頁面: {event_url}")
    page = await browser.get(event_url)
    await page.sleep(3)

    url = await page.evaluate("window.location.href")
    print(f"目前頁面: {url}")

    # 如果在 game 頁，點進第一個場次
    if "/activity/game/" in url:
        href = await page.evaluate("""
            (() => {
                const rows = document.querySelectorAll('#gameList > table > tbody > tr');
                for (const row of rows) {
                    const btn = row.querySelector('button[data-href]');
                    if (btn) return btn.getAttribute('data-href');
                }
                return null;
            })()
        """)
        if href:
            if href.startswith("/"):
                href = f"https://tixcraft.com{href}"
            print(f"進入場次: {href}")
            await page.get(href)
            await page.sleep(2)

    # 逐步導航到 ticket 頁面
    for attempt in range(5):
        await page.sleep(1)
        url = await page.evaluate("window.location.href")

        if "/ticket/ticket/" in url:
            break
        elif "/ticket/area/" in url:
            # 點第一個可用區域
            area_link = await page.evaluate("""
                (() => {
                    const links = document.querySelectorAll('.zone a');
                    for (const a of links) {
                        if (a.href && !a.classList.contains('disabled')) return a.href;
                    }
                    return null;
                })()
            """)
            if area_link:
                print(f"進入區域: {area_link}")
                await page.get(area_link)
                await page.sleep(2)
            else:
                print("⚠️  沒有可用區域")
                break
        elif "/activity/game/" in url:
            # 還在場次頁，點第一個場次
            href = await page.evaluate("""
                (() => {
                    const btns = document.querySelectorAll('button[data-href]');
                    return btns.length > 0 ? btns[0].getAttribute('data-href') : null;
                })()
            """)
            if href:
                if href.startswith("/"):
                    href = f"https://tixcraft.com{href}"
                print(f"進入場次: {href}")
                await page.get(href)
                await page.sleep(2)
            else:
                print("⚠️  沒有可用場次")
                break
        elif "/activity/verify/" in url:
            # 驗證頁面 — 嘗試自動回答
            answer = await page.evaluate("""
                (() => {
                    const zone = document.querySelector('.zone-verify');
                    if (!zone) return null;
                    let text = zone.textContent.replace('「','【').replace('」','】');
                    const m = text.match(/【(.+?)】/);
                    return m ? m[1] : null;
                })()
            """)
            if answer:
                print(f"驗證問答答案: {answer}")
                inp = await page.select("#checkCode")
                if inp:
                    await inp.send_keys(answer)
                btn = await page.select("#submitButton")
                if btn:
                    await btn.click()
                await page.sleep(2)

    url = await page.evaluate("window.location.href")
    if "/ticket/ticket/" not in url:
        print(f"⚠️  未能進入票務頁面 ({url})")
        print("可能原因：所有場次/區域售完，或需要登入")
        print("請確認 tixcraft 有可購買的場次後重試")
        browser.stop()
        return

    # 取得 cookies 和 headers
    cookies_str = await page.evaluate("document.cookie")
    ua = await page.evaluate("navigator.userAgent")
    cookies = {}
    for item in cookies_str.split(";"):
        item = item.strip()
        if "=" in item:
            k, v = item.split("=", 1)
            cookies[k] = v

    headers = {"User-Agent": ua, "Referer": url, "Accept": "image/*,*/*;q=0.8"}

    print(f"\n開始收集驗證碼圖片 (目標: {count} 張)...\n")
    collected = 0
    errors = 0

    async with httpx.AsyncClient(cookies=cookies, headers=headers, timeout=15) as client:
        for i in range(count + 50):  # 多抓一些，容忍錯誤
            if collected >= count:
                break

            try:
                # Step 1: refresh captcha
                resp = await client.get(
                    "https://tixcraft.com/ticket/captcha",
                    params={"refresh": "1"},
                )

                content_type = resp.headers.get("content-type", "")
                if "json" in content_type:
                    data = resp.json()
                    img_url = data.get("url", "")
                    if not img_url:
                        errors += 1
                        continue
                    # Step 2: get image
                    img_resp = await client.get(f"https://tixcraft.com{img_url}")
                    img_bytes = img_resp.content
                else:
                    img_bytes = resp.content

                if len(img_bytes) < 100:
                    errors += 1
                    continue

                # 儲存
                ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
                filename = f"{ts}_unknown.png"
                filepath = output / filename
                filepath.write_bytes(img_bytes)
                collected += 1

                if collected % 10 == 0:
                    print(f"  已收集 {collected}/{count} 張 (錯誤: {errors})")

                # 短暫等待避免被限速
                await asyncio.sleep(0.3)

            except Exception as e:
                errors += 1
                if errors > 20:
                    print(f"\n錯誤太多 ({errors})，停止收集")
                    print(f"最後錯誤: {e}")
                    break
                await asyncio.sleep(1)

    print(f"\n收集完成！共 {collected} 張圖片，{errors} 個錯誤")
    print(f"儲存目錄: {output}")
    print("\n下一步: .venv/bin/python -m ticket_bot label")

    browser.stop()


def main():
    parser = argparse.ArgumentParser(description="批量收集 tixcraft 驗證碼圖片")
    parser.add_argument("--count", "-n", type=int, default=200, help="收集數量（預設 200）")
    parser.add_argument("--output", "-o", default="./captcha_samples", help="輸出目錄")
    parser.add_argument("--url", default="", help="活動 URL（留空從 config.yaml 讀取）")
    parser.add_argument("--browser", default="", help="瀏覽器路徑")
    args = parser.parse_args()

    event_url = args.url
    executable_path = args.browser

    if not event_url:
        try:
            sys.path.insert(0, "src")
            from ticket_bot.config import load_config
            cfg = load_config()
            ev = next((e for e in cfg.events if e.platform == "tixcraft"), None)
            if ev:
                event_url = ev.url
            executable_path = executable_path or cfg.browser.executable_path
        except Exception:
            pass

    if not event_url:
        event_url = input("請輸入 tixcraft 活動 URL: ").strip()

    asyncio.run(collect(args.count, args.output, event_url, executable_path))


if __name__ == "__main__":
    main()
