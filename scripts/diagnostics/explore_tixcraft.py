"""探索 tixcraft 每個頁面結構，找可以收集驗證碼的活動"""
import asyncio
import os
import nodriver as uc


async def explore():
    browser = await uc.start(
        headless=False,
        user_data_dir="./chrome_profile",
        browser_executable_path=os.getenv("BROWSER_EXECUTABLE_PATH", ""),
        lang="zh-TW",
    )

    # 列出所有活動 slug
    page = await browser.get("https://tixcraft.com/activity")
    await page.sleep(3)

    slugs = await page.evaluate("""
        (() => {
            const links = document.querySelectorAll('a[href*="/activity/detail/"]');
            const seen = new Set();
            const result = [];
            for (const a of links) {
                const slug = a.href.split('/').pop();
                if (!seen.has(slug)) {
                    seen.add(slug);
                    result.push(slug);
                }
            }
            return result;
        })()
    """)

    print(f"共 {len(slugs)} 個活動 slug\n")

    found = None

    def unwrap(val):
        """nodriver evaluate 有時回傳 {'type': 'string', 'value': ...} 格式"""
        if isinstance(val, dict) and "value" in val:
            return val["value"]
        return val

    def s(val):
        return str(unwrap(val)) if val else ""

    def i(val):
        try:
            return int(unwrap(val))
        except (TypeError, ValueError):
            return 0

    def b(val):
        return bool(unwrap(val))

    # 解包 slugs 列表
    slugs = [s(x) for x in slugs if s(x)]
    print(f"解包後: {slugs[:5]}")

    for slug in slugs:
        # 1. Game 頁面
        await page.get(f"https://tixcraft.com/activity/game/{slug}")
        await page.sleep(1.5)

        url = s(await page.evaluate("window.location.href"))

        # 被 401/identify 擋住
        body_short = s(await page.evaluate("(document.body?.innerText||'').substring(0,100)"))
        if "identify" in body_short.lower() or "verify" in url:
            print(f"  🔒 {slug:25} 被擋 (identify)")
            continue

        btns = i(await page.evaluate("document.querySelectorAll('button[data-href]').length"))
        if btns == 0:
            print(f"  ⬜ {slug:25} 無可用場次")
            continue

        # 2. 有按鈕 → 用 JS 模擬點擊（tixcraft 按鈕用 JS 跳轉，不能直接 goto）
        await page.evaluate("""
            (() => {
                const btn = document.querySelector('button[data-href]');
                if (btn) {
                    const href = btn.getAttribute('data-href');
                    if (href) window.location.href = href;
                }
            })()
        """)
        await page.sleep(2.5)

        area_url = s(await page.evaluate("window.location.href"))

        # 可能被導到 verify 頁
        if "/activity/verify/" in area_url:
            print(f"  🟡 {slug:25} {btns} 場次 → 驗證頁（需回答問題）")
            continue

        if "/ticket/area/" not in area_url:
            print(f"  ❓ {slug:25} {btns} 場次 → 非 area 頁 ({area_url[:50]})")
            continue

        avail = i(await page.evaluate("""
            (() => {
                let count = 0;
                const links = document.querySelectorAll('.zone a');
                for (const a of links) {
                    if (!a.classList.contains('disabled') && a.href) count++;
                }
                return count;
            })()
        """))

        total = i(await page.evaluate("document.querySelectorAll('.zone').length"))

        if avail == 0:
            print(f"  🔴 {slug:25} {btns} 場次 → area 全售完 ({total} 區)")
            continue

        # 3. 有可用區域 → 點進去看 ticket 頁
        zone_href = s(await page.evaluate("""
            (() => {
                const links = document.querySelectorAll('.zone a');
                for (const a of links) {
                    if (!a.classList.contains('disabled') && a.href) return a.href;
                }
                return '';
            })()
        """))

        if not zone_href:
            continue

        await page.get(zone_href)
        await page.sleep(1.5)

        ticket_url = s(await page.evaluate("window.location.href"))
        has_captcha = b(await page.evaluate("!!document.querySelector('#TicketForm_verifyCode')"))

        if "/ticket/ticket/" in ticket_url and has_captcha:
            print(f"  🎯 {slug:25} {btns} 場次 → {avail}/{total} 區可用 → 有驗證碼！")
            found = slug
            break
        else:
            print(f"  🟢 {slug:25} {btns} 場次 → {avail}/{total} 區可用 → ticket頁: {ticket_url[:50]} captcha={has_captcha}")

    if found:
        print(f"\n✅ 可以用這個活動收集驗證碼: {found}")
        print(f"   .venv/bin/python scripts/train/collect_captchas.py --url https://tixcraft.com/activity/game/{found} --count 200")
    else:
        print("\n❌ 目前 tixcraft 上找不到有驗證碼頁面的活動（全部售完）")
        print("   可能需要等新活動開賣或釋票")

    browser.stop()


asyncio.run(explore())
