"""從 tixcraft 真實票務頁面批量收集驗證碼圖片

重要：同一活動頁面連續抓會拿到重複圖片，必須輪換不同活動頁面。
腳本會自動輪換頁面，並用模型辨識 + hash 雙重去重：
  - ONNX 模型辨識文字，同頁面連續出現相同文字 → 判定重複，跳下一個活動
  - MD5 hash 去重，確保不存完全相同的圖

已確認可用的票務頁面：
  - 26_fujirock (FUJI ROCK FESTIVAL'26)
  - 26_tpe4811th (台北大巨蛋)

Usage:
    .venv/bin/python scripts/train/collect_tixcraft_captchas.py --count 300
    .venv/bin/python scripts/train/collect_tixcraft_captchas.py --count 300 --page "https://tixcraft.com/ticket/ticket/..."
"""

import argparse
import asyncio
import hashlib
import io
import json
import os
from datetime import datetime
from pathlib import Path

import numpy as np
from PIL import Image

OUTPUT_DIR = Path("captcha_training_data/tixcraft_samples")
MODEL_DIR = Path("model")


# ── ONNX 模型辨識器 ──

class CaptchaRecognizer:
    """用訓練好的 ONNX 模型辨識驗證碼文字，用於去重判斷"""

    def __init__(self):
        self.session = None
        self.idx_to_char = {}
        self.img_h = 64
        self.img_w = 160

    def load(self) -> bool:
        """載入模型，回傳是否成功"""
        onnx_path = MODEL_DIR / "captcha_model.onnx"
        meta_path = MODEL_DIR / "meta.json"
        if not onnx_path.exists() or not meta_path.exists():
            print("  ⚠ 找不到 ONNX 模型，僅用 hash 去重")
            return False

        import onnxruntime as ort
        meta = json.loads(meta_path.read_text())
        self.idx_to_char = {i + 1: c for i, c in enumerate(meta["charset"])}
        self.img_h = meta["img_h"]
        self.img_w = meta["img_w"]
        self.session = ort.InferenceSession(str(onnx_path))
        print(f"  ✓ 已載入 ONNX 模型 (val_acc={meta['best_val_acc']:.1%})")
        return True

    def predict(self, img_bytes: bytes) -> str:
        """辨識驗證碼圖片，回傳預測文字（空字串表示無法辨識）"""
        if not self.session:
            return ""
        try:
            img = Image.open(io.BytesIO(img_bytes)).convert("L").resize((self.img_w, self.img_h))
            arr = np.array(img, dtype=np.float32) / 255.0
            tensor = arr[np.newaxis, np.newaxis, :, :]
            output = self.session.run(None, {self.session.get_inputs()[0].name: tensor})[0]
            indices = output[:, 0, :].argmax(axis=1)
            chars = []
            prev = -1
            for idx in indices:
                if idx != 0 and idx != prev and idx in self.idx_to_char:
                    chars.append(self.idx_to_char[idx])
                prev = idx
            return "".join(chars)
        except Exception:
            return ""


# 已知活動 slugs — 腳本會自動從 game 頁面掃描 area_ids
KNOWN_ACTIVITY_SLUGS = [
    "26_softbankh",
    "26_fujirock",
    "26_tpe4811th",
    "26_lany",
    "26_ive",
    "26_exokh",
    "26_itzy",
    "26_bus",
    "26_anson",
    "26_treasure",
    "26_megaport",
    "26_monstax",
    "26_gem",
    "25_lioneers",
]

# 用戶確認可用的 fallback URL
FALLBACK_TICKET_PAGES = [
    "https://tixcraft.com/ticket/ticket/26_softbankh/21708/7/68",
]


def load_existing_hashes() -> set:
    """載入已收集圖片的 hash，用於去重"""
    hashes = set()
    if OUTPUT_DIR.exists():
        for f in OUTPUT_DIR.glob("tix_*.png"):
            data = f.read_bytes()
            hashes.add(hashlib.md5(data).hexdigest())
    print(f"  已有 {len(hashes)} 張不重複圖片的 hash")
    return hashes


async def scan_ticket_urls(browser) -> list[str]:
    """自動掃描所有活動：game 頁面 → area_ids → area 頁面 → ticket URLs"""
    import random

    ticket_urls = []

    # Step 1: 從 game 頁面掃出 area_ids
    all_areas: list[tuple[str, list[str]]] = []

    for slug in KNOWN_ACTIVITY_SLUGS:
        try:
            page = await browser.get(f"https://tixcraft.com/activity/game/{slug}")
            await page.sleep(4)

            area_str = await page.evaluate("""
                (() => {
                    const ids = new Set();
                    // <a href="/ticket/area/slug/12345">
                    document.querySelectorAll('a[href*="/ticket/area/"]').forEach(a => {
                        const m = a.href.match(/\\/ticket\\/area\\/[^/]+\\/(\\d+)/);
                        if (m) ids.add(m[1]);
                    });
                    // <button data-href="/ticket/area/slug/12345">
                    document.querySelectorAll('button[data-href*="/ticket/area/"]').forEach(b => {
                        const m = b.dataset.href.match(/\\/ticket\\/area\\/[^/]+\\/(\\d+)/);
                        if (m) ids.add(m[1]);
                    });
                    return Array.from(ids).join(',');
                })()
            """)

            if area_str:
                area_ids = area_str.split(",")
                all_areas.append((slug, area_ids))
                print(f"  ✓ {slug}: {len(area_ids)} 場次")
            else:
                print(f"  ✗ {slug}: 無可用場次")

            await page.sleep(2)
        except Exception as e:
            print(f"  ✗ {slug}: {e}")
            continue

    if not all_areas:
        return ticket_urls

    # Step 2: 從 area 頁面的 areaUrlList JS 變數直接拿完整 ticket URLs
    print(f"\n  掃描座位區...")
    for slug, area_ids in all_areas:
        found = 0
        sampled = area_ids[:5] if len(area_ids) > 5 else area_ids
        for area_id in sampled:
            try:
                page = await browser.get(f"https://tixcraft.com/ticket/area/{slug}/{area_id}")
                await page.sleep(4)

                urls_str = await page.evaluate("""
                    (() => {
                        // tixcraft 把完整 ticket URL 存在 script 裡的 areaUrlList 變數
                        // 先試直接存取
                        if (typeof areaUrlList !== 'undefined') {
                            return Object.values(areaUrlList).join(',');
                        }
                        // fallback: 從 script 標籤裡用 regex 抓
                        for (const s of document.querySelectorAll('script')) {
                            const m = s.textContent.match(/areaUrlList\\s*=\\s*(\\{[^}]+\\})/);
                            if (m) {
                                try {
                                    const obj = JSON.parse(m[1].replace(/'/g, '"'));
                                    return Object.values(obj).join(',');
                                } catch(e) {}
                            }
                        }
                        return '';
                    })()
                """)

                if urls_str:
                    for url in urls_str.split(","):
                        if "/ticket/ticket/" in url:
                            ticket_urls.append(url)
                            found += 1
            except Exception as e:
                continue
            await page.sleep(2)

        if found:
            print(f"    {slug}: {found} 個座位區")

    random.shuffle(ticket_urls)
    return ticket_urls


async def find_working_pages(browser, ticket_urls: list[str]) -> list[str]:
    """從 ticket URLs 中篩出有驗證碼的頁面"""
    working = []
    for url in ticket_urls:
        try:
            page = await browser.get(url)
            await page.sleep(3)

            has_captcha = await page.evaluate(
                "document.querySelector('img[src*=\"captcha\"]') !== null"
            )
            if has_captcha:
                working.append(url)
            # 找到夠多就停（不需要全部測）
            if len(working) >= 30:
                print(f"  已找到 {len(working)} 個有驗證碼的頁面，夠用了")
                break
        except Exception:
            continue
    return working


async def get_cookies_and_ua(browser, url: str) -> tuple[str, str]:
    """從一個有驗證碼的頁面取得 cookies 和 UA"""
    page = await browser.get(url)
    await page.sleep(3)
    cookies_str = await page.evaluate("document.cookie")
    ua = await page.evaluate("navigator.userAgent")
    return cookies_str, ua


async def collect_via_js(browser, pages_urls: list, count: int, seen_hashes: set,
                         seen_texts: set, recognizer: CaptchaRecognizer) -> int:
    """輪換不同活動頁面，用 JS 抓驗證碼圖片，模型辨識 + hash 雙重去重"""
    import base64

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    collected = 0
    errors = 0
    duplicates = 0
    # 記錄每個頁面最後的辨識結果
    last_text_per_page: dict[str, str] = {}
    # 頁面連續重複計數，超過閾值就暫時跳過
    stale_count: dict[str, int] = {u: 0 for u in pages_urls}
    page_idx = 0

    print(f"\n開始 JS 模式收集驗證碼 (目標: {count} 張, 輪換 {len(pages_urls)} 個頁面)...\n")

    for i in range(count * 5):
        if collected >= count:
            break

        # 智慧選頁：跳過連續重複太多的頁面
        attempts = 0
        while attempts < len(pages_urls):
            url = pages_urls[page_idx % len(pages_urls)]
            page_idx += 1
            if stale_count.get(url, 0) < 5:
                break
            attempts += 1
        else:
            # 所有頁面都 stale，重置計數重試
            stale_count = {u: 0 for u in pages_urls}
            url = pages_urls[page_idx % len(pages_urls)]
            page_idx += 1

        try:
            page = await browser.get(url)
            await page.sleep(2)

            img_b64 = await page.evaluate("""
                (async () => {
                    try {
                        const img = document.querySelector('img[src*="captcha"]');
                        if (!img) return null;
                        const newSrc = img.src.split('?')[0] + '?v=' + Math.random();
                        img.src = newSrc;
                        await new Promise((resolve, reject) => {
                            img.onload = resolve;
                            img.onerror = reject;
                            setTimeout(resolve, 5000);
                        });
                        await new Promise(r => setTimeout(r, 300));
                        const canvas = document.createElement('canvas');
                        canvas.width = img.naturalWidth || img.width;
                        canvas.height = img.naturalHeight || img.height;
                        if (canvas.width === 0 || canvas.height === 0) return null;
                        canvas.getContext('2d').drawImage(img, 0, 0);
                        return canvas.toDataURL('image/png').split(',')[1];
                    } catch(e) { return null; }
                })()
            """)

            if not img_b64:
                errors += 1
                if errors > 50:
                    print(f"  錯誤太多 ({errors})，停止")
                    break
                continue

            raw = base64.b64decode(img_b64)
            if len(raw) < 100:
                errors += 1
                continue

            # 模型辨識去重
            text = recognizer.predict(raw)
            if text:
                if text == last_text_per_page.get(url):
                    stale_count[url] = stale_count.get(url, 0) + 1
                    duplicates += 1
                    if duplicates % 10 == 0:
                        print(f"  模型判重 {duplicates} 張 ('{text}' 在 ...{url[-20:]} 連續出現)")
                    continue
                if text in seen_texts:
                    duplicates += 1
                    continue
                last_text_per_page[url] = text
                stale_count[url] = 0

            # hash 去重（兜底）
            h = hashlib.md5(raw).hexdigest()
            if h in seen_hashes:
                duplicates += 1
                continue

            seen_hashes.add(h)
            if text:
                seen_texts.add(text)
            ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            filepath = OUTPUT_DIR / f"tix_{ts}.png"
            filepath.write_bytes(raw)
            collected += 1

            if collected % 10 == 0:
                print(f"  已收集 {collected}/{count} (重複: {duplicates}, 錯誤: {errors})")

        except Exception as e:
            errors += 1
            if errors % 20 == 0:
                print(f"  錯誤 {errors}: {e}")
            await asyncio.sleep(3)

    print(f"  JS 模式完成: 收集 {collected}, 重複跳過 {duplicates}, 錯誤 {errors}")
    return collected


async def collect_via_http(cookies_str: str, ua: str, pages_urls: list, count: int,
                           seen_hashes: set, seen_texts: set, recognizer: CaptchaRecognizer) -> int:
    """輪換不同活動頁面，用 httpx 抓 captcha API，模型辨識 + hash 雙重去重"""
    import httpx

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    cookies = {}
    for item in cookies_str.split(";"):
        item = item.strip()
        if "=" in item:
            k, v = item.split("=", 1)
            cookies[k.strip()] = v.strip()

    collected = 0
    errors = 0
    duplicates = 0
    last_text_per_page: dict[str, str] = {}
    stale_count: dict[str, int] = {u: 0 for u in pages_urls}
    page_idx = 0

    print(f"\n開始用 HTTP API 收集驗證碼 (目標: {count} 張, 輪換 {len(pages_urls)} 個頁面)...\n")

    async with httpx.AsyncClient(cookies=cookies, timeout=15) as client:
        for i in range(count * 5):
            if collected >= count:
                break

            # 智慧選頁：跳過連續重複太多的頁面
            attempts = 0
            while attempts < len(pages_urls):
                page_url = pages_urls[page_idx % len(pages_urls)]
                page_idx += 1
                if stale_count.get(page_url, 0) < 5:
                    break
                attempts += 1
            else:
                stale_count = {u: 0 for u in pages_urls}
                page_url = pages_urls[page_idx % len(pages_urls)]
                page_idx += 1

            headers = {
                "User-Agent": ua,
                "Referer": page_url,
                "Accept": "image/*,*/*;q=0.8",
            }

            try:
                # 先訪問票務頁面以刷新 server-side captcha session
                await client.get(page_url, headers={"User-Agent": ua}, follow_redirects=True)

                resp = await client.get(
                    "https://tixcraft.com/ticket/captcha",
                    headers=headers,
                    params={"v": f"{datetime.now().timestamp()}"},
                )

                raw = None
                ct = resp.headers.get("content-type", "")

                if "image" in ct and len(resp.content) > 100:
                    raw = resp.content
                elif "json" in ct:
                    data = resp.json()
                    img_url = data.get("url", "")
                    if img_url:
                        full_url = f"https://tixcraft.com{img_url}" if img_url.startswith("/") else img_url
                        img_resp = await client.get(full_url, headers=headers)
                        if len(img_resp.content) > 100:
                            raw = img_resp.content

                if not raw:
                    errors += 1
                    continue

                # 模型辨識去重
                text = recognizer.predict(raw)
                if text:
                    if text == last_text_per_page.get(page_url):
                        stale_count[page_url] = stale_count.get(page_url, 0) + 1
                        duplicates += 1
                        if duplicates % 10 == 0:
                            print(f"  模型判重 {duplicates} 張 ('{text}' 在 ...{page_url[-20:]} 連續出現)")
                        continue
                    if text in seen_texts:
                        duplicates += 1
                        continue
                    last_text_per_page[page_url] = text
                    stale_count[page_url] = 0

                # hash 去重（兜底）
                h = hashlib.md5(raw).hexdigest()
                if h in seen_hashes:
                    duplicates += 1
                    continue

                seen_hashes.add(h)
                if text:
                    seen_texts.add(text)
                ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
                filepath = OUTPUT_DIR / f"tix_{ts}.png"
                filepath.write_bytes(raw)
                collected += 1

                if collected % 10 == 0 and collected > 0:
                    print(f"  已收集 {collected}/{count} (重複: {duplicates}, 錯誤: {errors})")

                await asyncio.sleep(2.5)

            except Exception as e:
                errors += 1
                if errors % 20 == 0:
                    print(f"  錯誤 {errors}: {e}")
                if errors > 50:
                    print("  錯誤太多，停止")
                    break
                await asyncio.sleep(3)

    print(f"  HTTP 模式完成: 收集 {collected}, 重複跳過 {duplicates}, 錯誤 {errors}")
    return collected


async def main(count: int, page_url: str):
    import nodriver as uc

    # 載入 ONNX 模型
    recognizer = CaptchaRecognizer()
    recognizer.load()

    # 載入已有圖片的 hash 用於去重
    seen_hashes = load_existing_hashes()
    seen_texts: set[str] = set()

    print("啟動瀏覽器...")
    browser = await uc.start(
        headless=False,
        user_data_dir="./chrome_profile",
        lang="zh-TW",
        browser_executable_path=os.getenv("BROWSER_EXECUTABLE_PATH", ""),
    )

    if page_url:
        pages_urls = [page_url]
    else:
        # 自動掃描所有活動的 area 頁面，組出 ticket URLs
        print("\n掃描活動場次與座位區...")
        ticket_urls = await scan_ticket_urls(browser)

        if not ticket_urls:
            print("沒有掃到任何座位區，使用 fallback URLs")
            ticket_urls = FALLBACK_TICKET_PAGES

        print(f"\n共 {len(ticket_urls)} 個 ticket URLs，篩選有驗證碼的頁面...")
        pages_urls = await find_working_pages(browser, ticket_urls)

        if not pages_urls:
            print("找不到任何有驗證碼的頁面")
            browser.stop()
            return

        print(f"\n找到 {len(pages_urls)} 個有驗證碼的頁面，將輪換收集\n")

    # 取得 cookies 和 UA
    cookies_str, ua = await get_cookies_and_ua(browser, pages_urls[0])

    # 先嘗試 HTTP API（更快），失敗則用 JS 方式
    print("\n嘗試 HTTP API 模式...")
    http_count = await collect_via_http(cookies_str, ua, pages_urls, count,
                                         seen_hashes, seen_texts, recognizer)

    if http_count < count:
        remaining = count - http_count
        print(f"\nHTTP 模式收集了 {http_count} 張不重複圖，用 JS 模式補齊剩餘 {remaining} 張...")
        js_count = await collect_via_js(browser, pages_urls, remaining,
                                         seen_hashes, seen_texts, recognizer)
        total = http_count + js_count
    else:
        total = http_count

    print(f"\n{'='*50}")
    print(f"  收集完成！共 {total} 張不重複的 tixcraft 驗證碼")
    print(f"  目錄: {OUTPUT_DIR}")
    print(f"{'='*50}")

    # 統計檔案
    files = list(OUTPUT_DIR.glob("tix_*.png"))
    sizes = [f.stat().st_size for f in files]
    if sizes:
        print(f"\n  檔案數: {len(files)}")
        print(f"  平均大小: {sum(sizes)//len(sizes)} bytes")
        print(f"  最小: {min(sizes)} bytes, 最大: {max(sizes)} bytes")

    print(f"\n下一步: 人工標註")
    print(f"  .venv/bin/python -m ticket_bot label --dir {OUTPUT_DIR}")

    browser.stop()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="從 tixcraft 收集真實驗證碼")
    parser.add_argument("--count", "-n", type=int, default=300, help="收集數量")
    parser.add_argument("--page", default="", help="指定票務頁面 URL")
    args = parser.parse_args()
    asyncio.run(main(args.count, args.page))
