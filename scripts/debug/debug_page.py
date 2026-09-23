"""Debug: 開啟場次頁面，dump DOM 結構"""
import asyncio
import os
import nodriver as uc

async def main():
    browser = await uc.start(
        headless=False,
        user_data_dir="./chrome_profile",
        browser_executable_path=os.getenv("BROWSER_EXECUTABLE_PATH", ""),
        lang="zh-TW",
    )
    page = await browser.get("https://tixcraft.com/activity/game/26_softbankh")
    await page.sleep(5)

    # Dump gameList area
    html = await page.evaluate("""
        (() => {
            const gameList = document.querySelector('#gameList');
            if (gameList) return gameList.outerHTML;
            const tables = document.querySelectorAll('table');
            if (tables.length) return Array.from(tables).map(t => t.outerHTML).join('\\n---\\n');
            const main = document.querySelector('.container') || document.querySelector('main') || document.body;
            return main.innerHTML.substring(0, 8000);
        })()
    """)
    print("=== PAGE STRUCTURE ===")
    print(html[:6000])

    # Also check for all buttons/links
    buttons = await page.evaluate("""
        (() => {
            const btns = document.querySelectorAll('button, a.btn, input[type=submit], .btn-next, [class*=btn]');
            return Array.from(btns).map(b => ({
                tag: b.tagName,
                cls: b.className,
                text: b.textContent.trim().substring(0, 80),
                href: b.href || '',
                id: b.id,
            }));
        })()
    """)
    print("\n=== BUTTONS/LINKS ===")
    for b in buttons:
        print(f"  <{b['tag']} class='{b['cls']}' id='{b['id']}'> {b['text'][:60]}  {b['href'][:80]}")

    await page.sleep(1)
    browser.stop()

asyncio.run(main())
