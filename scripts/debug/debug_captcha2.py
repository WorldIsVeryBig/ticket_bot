"""Debug: 下載驗證碼圖片並用 ddddocr 辨識"""
import asyncio
import os
import nodriver as uc
import httpx
import ddddocr

async def main():
    browser = await uc.start(
        headless=False,
        user_data_dir="./chrome_profile",
        browser_executable_path=os.getenv("BROWSER_EXECUTABLE_PATH", ""),
        lang="zh-TW",
    )
    # 先到場次頁 → 點進票務頁
    page = await browser.get("https://tixcraft.com/activity/game/26_softbankh")
    await page.sleep(3)

    # 進入一個有票的場次
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
        if href.startswith("/"): href = f"https://tixcraft.com{href}"
        await page.get(href)
        await page.sleep(2)

        # 點第一個區域
        first_link = await page.evaluate("""
            (() => {
                const zone = document.querySelector('.zone a');
                if (zone) { return zone.href; }
                return null;
            })()
        """)
        if first_link:
            await page.get(first_link)
            await page.sleep(2)

    # 現在應該在 /ticket/ticket/ 頁面
    url = await page.evaluate("window.location.href")
    print(f"Current URL: {url}")

    cookies_str = await page.evaluate("document.cookie")
    ua = await page.evaluate("navigator.userAgent")
    cookies = {}
    for item in cookies_str.split(";"):
        item = item.strip()
        if "=" in item:
            k, v = item.split("=", 1)
            cookies[k] = v

    headers = {"User-Agent": ua, "Referer": url, "Accept": "image/*,*/*;q=0.8"}

    async with httpx.AsyncClient(cookies=cookies, headers=headers) as client:
        # Step 1: refresh
        resp = await client.get("https://tixcraft.com/ticket/captcha", params={"refresh": "1"}, timeout=10)
        print(f"Refresh response: {resp.text}")

        content_type = resp.headers.get("content-type", "")
        if "json" in content_type:
            data = resp.json()
            img_url = data.get("url", "")
            # Step 2: get image
            img_resp = await client.get(f"https://tixcraft.com{img_url}", timeout=10)
            img_bytes = img_resp.content
        else:
            img_bytes = resp.content

        print(f"Image size: {len(img_bytes)} bytes")
        print(f"Image type header: {img_resp.headers.get('content-type')}")
        print(f"First 20 bytes: {img_bytes[:20]}")

        # Save image
        with open("/tmp/captcha_sample.png", "wb") as f:
            f.write(img_bytes)
        print("Saved to /tmp/captcha_sample.png")

        # Try OCR
        ocr = ddddocr.DdddOcr(beta=True)
        ocr.set_ranges(1)
        result = ocr.classification(img_bytes, probability=True)
        print(f"\nOCR result: text='{result['text']}' confidence={result['confidence']:.3f}")

        # Try without beta
        ocr2 = ddddocr.DdddOcr(beta=False)
        result2 = ocr2.classification(img_bytes, probability=True)
        print(f"OCR (no beta): text='{result2['text']}' confidence={result2['confidence']:.3f}")

        # Try without ranges
        ocr3 = ddddocr.DdddOcr(beta=True)
        result3 = ocr3.classification(img_bytes, probability=True)
        print(f"OCR (no ranges): text='{result3['text']}' confidence={result3['confidence']:.3f}")

    await page.sleep(1)
    browser.stop()

asyncio.run(main())
