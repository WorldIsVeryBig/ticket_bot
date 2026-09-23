"""Debug: 用瀏覽器 session 下載驗證碼並檢查格式"""
import asyncio
import os
import nodriver as uc
import httpx

async def main():
    browser = await uc.start(
        headless=False,
        user_data_dir="./chrome_profile",
        browser_executable_path=os.getenv("BROWSER_EXECUTABLE_PATH", ""),
        lang="zh-TW",
    )
    # 先進入訂票頁面取得 session
    page = await browser.get("https://tixcraft.com/activity/game/26_softbankh")
    await page.sleep(3)

    # 取 cookies + UA
    cookies_str = await page.evaluate("document.cookie")
    ua = await page.evaluate("navigator.userAgent")
    cookies = {}
    for item in cookies_str.split(";"):
        item = item.strip()
        if "=" in item:
            k, v = item.split("=", 1)
            cookies[k] = v

    print(f"Cookies: {list(cookies.keys())}")
    print(f"UA: {ua}")

    # 嘗試下載 captcha
    headers = {
        "User-Agent": ua,
        "Referer": "https://tixcraft.com/ticket/ticket/26_softbankh/21724",
        "Accept": "image/*,*/*;q=0.8",
    }
    async with httpx.AsyncClient(cookies=cookies, headers=headers) as client:
        resp = await client.get("https://tixcraft.com/ticket/captcha", params={"refresh": "1"}, timeout=10)
        print(f"\nStatus: {resp.status_code}")
        print(f"Content-Type: {resp.headers.get('content-type')}")
        print(f"Content length: {len(resp.content)}")
        print(f"First 50 bytes: {resp.content[:50]}")

        # 存檔看看
        with open("/tmp/captcha_debug.bin", "wb") as f:
            f.write(resp.content)
        print("Saved to /tmp/captcha_debug.bin")

    await page.sleep(1)
    browser.stop()

asyncio.run(main())
