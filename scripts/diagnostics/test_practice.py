"""
Practice site simulation - test full ticket grabbing flow
Target: https://ticket-training.onrender.com
"""

import asyncio
import time

from playwright.async_api import async_playwright

SITE = "https://ticket-training.onrender.com"


async def run():
    start = time.time()

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()

        # ── Step 1: 直接進入選區頁面（跳過首頁倒數計時模擬）──
        print("[1] 進入選區頁面...")
        await page.goto(f"{SITE}/progress")
        await page.wait_for_load_state("domcontentloaded")
        print(f"    → 進入選區頁面 ({time.time() - start:.2f}s)")

        # ── Step 2: 選擇座位區域 (第一個可用) ──
        print("[2] 選擇座位區域...")
        # 用 JS 點第一個座位區域
        clicked = await page.evaluate("""
            () => {
                // 找所有含 goToChecking 的 onclick 元素
                const els = document.querySelectorAll('[onclick*="goToChecking"]');
                if (els.length > 0) {
                    els[0].click();
                    return els[0].textContent.trim().substring(0, 40);
                }
                // fallback: 直接呼叫
                if (typeof goToChecking === 'function') {
                    goToChecking('特A區', 6880, '橙');
                    return '特A區 (fallback)';
                }
                return null;
            }
        """)
        if not clicked:
            # 直接導航 fallback
            await page.goto(f"{SITE}/checking?seat=特A區&price=6880&color=橙")
            clicked = "特A區 (direct nav)"
        await page.wait_for_url("**/checking**")
        print(f"    → 選擇: {clicked} ({time.time() - start:.2f}s)")

        # ── Step 3: 填寫訂票表單 ──
        print("[3] 填寫訂票表單...")

        # 先截圖看一下頁面結構
        await page.wait_for_load_state("domcontentloaded")
        await page.wait_for_timeout(500)

        # 偵測頁面上所有表單元素
        form_info = await page.evaluate("""
            () => {
                const selects = [...document.querySelectorAll('select')].map(s => ({
                    tag: 'select', id: s.id, class: s.className, name: s.name,
                    options: [...s.options].map(o => o.value)
                }));
                const inputs = [...document.querySelectorAll('input')].map(i => ({
                    tag: 'input', id: i.id, class: i.className, name: i.name, type: i.type
                }));
                const buttons = [...document.querySelectorAll('button')].map(b => ({
                    tag: 'button', id: b.id, class: b.className, text: b.textContent.trim()
                }));
                const imgs = [...document.querySelectorAll('img')].map(i => ({
                    tag: 'img', id: i.id, src: i.src, answer: i.dataset?.answer
                }));
                return { selects, inputs, buttons, imgs, url: location.href };
            }
        """)
        print(f"    URL: {form_info['url']}")
        for s in form_info['selects']:
            print(f"    SELECT: id={s['id']} class={s['class']} options={s['options']}")
        for i in form_info['inputs']:
            print(f"    INPUT: id={i['id']} class={i['class']} type={i['type']}")
        for b in form_info['buttons']:
            print(f"    BUTTON: id={b['id']} text={b['text']}")
        for i in form_info['imgs']:
            if i['id'] or 'captcha' in (i.get('src') or ''):
                print(f"    IMG: id={i['id']} answer={i.get('answer')}")

        # 3a. 選票數 (用找到的第一個 select)
        if form_info['selects']:
            sel = form_info['selects'][0]
            selector = f"#{sel['id']}" if sel['id'] else f"select.{sel['class'].split()[0]}" if sel['class'] else "select"
            await page.select_option(selector, "2")
            print(f"    → 已選 2 張票 (selector: {selector})")

        # 3b. 驗證碼
        captcha_answer = await page.evaluate("""
            () => {
                const img = document.querySelector('[id*="captcha"] img, img[id*="captcha"], #captcha-image');
                return img?.dataset?.answer || null;
            }
        """)
        if not captcha_answer:
            await page.wait_for_timeout(1000)
            captcha_answer = await page.evaluate("""
                () => {
                    const imgs = document.querySelectorAll('img');
                    for (const img of imgs) {
                        if (img.dataset?.answer) return img.dataset.answer;
                        if (img.src?.includes('captcha')) return img.src.split('/').pop().split('.')[0];
                    }
                    return null;
                }
            """)

        if captcha_answer:
            print(f"    → 驗證碼答案: {captcha_answer}")
        else:
            print("    → 無法取得驗證碼，嘗試從 API 取得...")
            captcha_answer = await page.evaluate("""
                async () => {
                    const r = await fetch('/captcha');
                    const d = await r.json();
                    return d.filename?.split('.')[0] || null;
                }
            """)
            print(f"    → 從 API 取得答案: {captcha_answer}")

        # 找驗證碼 input 並填入
        captcha_input = form_info['inputs']
        captcha_selector = None
        for inp in captcha_input:
            if 'captcha' in (inp['id'] + inp['class'] + inp['name']).lower():
                captcha_selector = f"#{inp['id']}" if inp['id'] else f"input.{inp['class'].split()[0]}"
                break
        if not captcha_selector:
            # fallback: 找 text input
            for inp in captcha_input:
                if inp['type'] == 'text':
                    captcha_selector = f"#{inp['id']}" if inp['id'] else "input[type=text]"
                    break

        if captcha_selector and captcha_answer:
            await page.fill(captcha_selector, captcha_answer)
            print(f"    → 驗證碼已填入 (selector: {captcha_selector})")

        # 3c. 勾選同意條款
        checkbox = None
        for inp in captcha_input:
            if inp['type'] == 'checkbox':
                checkbox = f"#{inp['id']}" if inp['id'] else "input[type=checkbox]"
                break
        if checkbox:
            await page.check(checkbox)
            print(f"    → 已勾選同意條款 (selector: {checkbox})")

        # 3d. 送出
        print("[4] 送出表單...")
        submit_btn = None
        for b in form_info['buttons']:
            if '確認' in b['text']:
                submit_btn = f"#{b['id']}" if b['id'] else f"text={b['text']}"
                break
        if submit_btn:
            await page.click(submit_btn)
        else:
            await page.click("button[type=submit]")

        # 等待跳轉到 finish
        try:
            await page.wait_for_url("**/finish**", timeout=5000)
        except Exception:
            print("    → 第一次送出失敗，重試...")
            captcha_answer = await page.evaluate("""
                async () => {
                    const r = await fetch('/captcha');
                    const d = await r.json();
                    const img = document.querySelector('img[id*="captcha"], #captcha-image');
                    if (img) img.dataset.answer = d.filename.split('.')[0];
                    return d.filename.split('.')[0];
                }
            """)
            if captcha_selector and captcha_answer:
                await page.fill(captcha_selector, captcha_answer)
                if submit_btn:
                    await page.click(submit_btn)
                await page.wait_for_url("**/finish**", timeout=5000)

        elapsed = time.time() - start
        print(f"\n{'='*50}")
        print(f"    搶票成功! 總耗時: {elapsed:.2f} 秒")
        print(f"{'='*50}")

        # 截圖留存
        await page.screenshot(path="practice_result.png")
        print("    → 截圖儲存: practice_result.png")

        await browser.close()


if __name__ == "__main__":
    asyncio.run(run())
