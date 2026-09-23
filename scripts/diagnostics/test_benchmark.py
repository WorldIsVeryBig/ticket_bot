"""
搶票模擬考 — 連續多輪壓測 + ddddocr 準確率測試
Target: https://ticket-training.onrender.com
"""

import asyncio
import json
import time
import statistics

from playwright.async_api import async_playwright

SITE = "https://ticket-training.onrender.com"
ROUNDS = 50


async def run_round(page, solver, round_num: int) -> dict:
    """跑一輪完整搶票流程，回傳計時 + 驗證碼結果"""
    result = {
        "round": round_num,
        "success": False,
        "ocr_correct": None,
        "ocr_text": "",
        "captcha_answer": "",
        "times": {},
        "error": None,
    }

    t0 = time.time()

    try:
        # ── Step 1: 選區頁面 ──
        await page.goto(f"{SITE}/progress", wait_until="domcontentloaded")
        t1 = time.time()
        result["times"]["load_progress"] = t1 - t0

        # ── Step 2: 選座位 ──
        clicked = await page.evaluate("""
            () => {
                const els = document.querySelectorAll('[onclick*="goToChecking"]');
                if (els.length > 0) { els[0].click(); return true; }
                return false;
            }
        """)
        if not clicked:
            await page.goto(
                f"{SITE}/checking?seat=特A區&price=6880&color=%23e29bb9",
                wait_until="domcontentloaded",
            )
        else:
            await page.wait_for_url("**/checking**", timeout=10000)
        await page.wait_for_load_state("domcontentloaded")
        t2 = time.time()
        result["times"]["select_area"] = t2 - t1

        # ── Step 3: 等表單載入 ──
        await page.wait_for_timeout(300)

        # ── Step 3a: 選票數 ──
        await page.select_option("select.quantity-select", "2")

        # ── Step 3b: 取得驗證碼圖片 + OCR ──
        # 從頁面取得正確答案（用於比對 OCR）
        captcha_answer = await page.evaluate("""
            () => document.getElementById('captcha-image')?.dataset?.answer || null
        """)
        result["captcha_answer"] = captcha_answer or ""

        # 下載驗證碼圖片做 OCR
        captcha_img_bytes = None
        captcha_src = await page.evaluate("""
            () => document.getElementById('captcha-image')?.src || null
        """)
        if captcha_src:
            resp = await page.request.get(captcha_src)
            if resp.ok:
                captcha_img_bytes = await resp.body()

        ocr_text = ""
        ocr_confidence = 0.0
        if captcha_img_bytes and solver:
            try:
                ocr_text, ocr_confidence = solver.solve(captcha_img_bytes)
            except Exception as e:
                ocr_text = f"[OCR error: {e}]"

        result["ocr_text"] = ocr_text
        result["ocr_confidence"] = ocr_confidence
        if captcha_answer and ocr_text and not ocr_text.startswith("["):
            result["ocr_correct"] = ocr_text.strip().lower() == captcha_answer.strip().lower()
        else:
            result["ocr_correct"] = None

        t3 = time.time()
        result["times"]["captcha_ocr"] = t3 - t2

        # ── Step 3c: 填入驗證碼（用正確答案以確保流程完整） ──
        await page.fill("#captcha-input", captcha_answer or ocr_text)

        # ── Step 3d: 勾選同意 ──
        await page.check("#terms-checkbox")

        t4 = time.time()
        result["times"]["fill_form"] = t4 - t3

        # ── Step 4: 送出 ──
        await page.click("text=確認張數")

        try:
            await page.wait_for_url("**/finish**", timeout=5000)
            result["success"] = True
        except Exception:
            # 重試一次（captcha 可能已刷新）
            new_answer = await page.evaluate("""
                () => document.getElementById('captcha-image')?.dataset?.answer || null
            """)
            if new_answer:
                await page.fill("#captcha-input", new_answer)
                await page.click("text=確認張數")
                try:
                    await page.wait_for_url("**/finish**", timeout=5000)
                    result["success"] = True
                except Exception:
                    result["error"] = "submit_failed_after_retry"
            else:
                result["error"] = "submit_failed"

        t5 = time.time()
        result["times"]["submit"] = t5 - t4
        result["times"]["total"] = t5 - t0

    except Exception as e:
        result["error"] = str(e)
        result["times"]["total"] = time.time() - t0

    return result


async def main():
    # 初始化 ddddocr（練習站用英文字母，設 char_ranges=0 不限制字元）
    print("初始化 ddddocr...")
    from ticket_bot.captcha.solver import CaptchaSolver
    from ticket_bot.config import CaptchaConfig

    captcha_cfg = CaptchaConfig(char_ranges=0, preprocess=False)
    solver = CaptchaSolver(captcha_cfg)
    print(f"ddddocr 就緒 (ranges={captcha_cfg.char_ranges}, preprocess={captcha_cfg.preprocess})\n")

    results = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()

        print(f"{'='*70}")
        print(f"  搶票模擬考 — {ROUNDS} 輪連續壓測")
        print(f"{'='*70}\n")

        for i in range(1, ROUNDS + 1):
            result = await run_round(page, solver, i)
            results.append(result)

            status = "✓" if result["success"] else "✗"
            ocr_text = result.get("ocr_text", "")
            answer = result.get("captcha_answer", "")
            conf = result.get("ocr_confidence", 0)

            if result["ocr_correct"] is True:
                ocr_mark = f"OCR ✓ '{ocr_text}'"
            elif result["ocr_correct"] is False:
                ocr_mark = f"OCR ✗ '{ocr_text}' ≠ '{answer}'"
            else:
                ocr_mark = f"OCR ? '{ocr_text}' / '{answer}'"

            total = result["times"].get("total", 0)
            print(
                f"  [{status}] 第 {i:2d} 輪  "
                f"耗時 {total:5.2f}s  "
                f"| {ocr_mark:40s} "
                f"| conf={conf:.2f}"
            )
            if result["error"]:
                print(f"       └─ 錯誤: {result['error']}")

        await browser.close()

    # ── 統計報告 ──
    print(f"\n{'='*70}")
    print("  統計報告")
    print(f"{'='*70}\n")

    # 成功率
    successes = [r for r in results if r["success"]]
    print(f"  搶票成功率: {len(successes)}/{len(results)} ({len(successes)/len(results)*100:.0f}%)")

    # OCR 準確率
    ocr_results = [r for r in results if r["ocr_correct"] is not None]
    ocr_correct = [r for r in ocr_results if r["ocr_correct"]]
    if ocr_results:
        print(f"  OCR 準確率:  {len(ocr_correct)}/{len(ocr_results)} ({len(ocr_correct)/len(ocr_results)*100:.0f}%)")
    else:
        print("  OCR 準確率:  無資料")

    # OCR 錯誤明細
    ocr_wrong = [r for r in ocr_results if not r["ocr_correct"]]
    if ocr_wrong:
        print(f"\n  OCR 錯誤明細:")
        for r in ocr_wrong:
            print(f"    第 {r['round']:2d} 輪: OCR='{r['ocr_text']}' 正確='{r['captcha_answer']}' conf={r.get('ocr_confidence', 0):.2f}")

    # 計時統計
    totals = [r["times"].get("total", 0) for r in successes]
    if totals:
        print(f"\n  計時統計 (成功的 {len(successes)} 輪):")
        print(f"    平均耗時: {statistics.mean(totals):.2f}s")
        print(f"    最快:     {min(totals):.2f}s")
        print(f"    最慢:     {max(totals):.2f}s")
        if len(totals) > 1:
            print(f"    標準差:   {statistics.stdev(totals):.2f}s")

    # 各步驟平均耗時
    step_names = ["load_progress", "select_area", "captcha_ocr", "fill_form", "submit"]
    step_labels = {
        "load_progress": "載入選區頁",
        "select_area": "選座位區域",
        "captcha_ocr": "驗證碼 OCR",
        "fill_form": "填表",
        "submit": "送出",
    }
    print(f"\n  各步驟平均耗時:")
    for step in step_names:
        times = [r["times"].get(step, 0) for r in successes if step in r["times"]]
        if times:
            print(f"    {step_labels.get(step, step):12s}: {statistics.mean(times):.3f}s (max {max(times):.3f}s)")

    # OCR 信心度分布
    confidences = [r.get("ocr_confidence", 0) for r in ocr_results]
    if confidences:
        print(f"\n  OCR 信心度:")
        print(f"    平均: {statistics.mean(confidences):.2f}")
        print(f"    最低: {min(confidences):.2f}")
        print(f"    最高: {max(confidences):.2f}")

    # 修正建議
    print(f"\n{'='*70}")
    print("  修正建議")
    print(f"{'='*70}\n")

    issues = []

    if ocr_results and len(ocr_correct) / len(ocr_results) < 0.7:
        issues.append(
            "  [HIGH] OCR 準確率不足 70%\n"
            "    → 考慮: 訓練自定義模型、多次 OCR 取最常見結果、或信心度不足時自動重新載入驗證碼"
        )

    if totals and statistics.mean(totals) > 5.0:
        issues.append(
            "  [MED] 平均耗時超過 5 秒\n"
            "    → 考慮: 減少 sleep 時間、預先載入頁面、用 API 模式跳過瀏覽器渲染"
        )

    slow_steps = {}
    for step in step_names:
        times = [r["times"].get(step, 0) for r in successes if step in r["times"]]
        if times and max(times) > 2.0:
            slow_steps[step] = max(times)
    for step, max_time in slow_steps.items():
        issues.append(
            f"  [MED] {step_labels.get(step, step)} 最大耗時 {max_time:.2f}s\n"
            f"    → 此步驟偶爾過慢，可能是網路延遲或頁面載入不穩定"
        )

    if len(successes) < len(results):
        fail_count = len(results) - len(successes)
        issues.append(
            f"  [HIGH] 有 {fail_count} 輪搶票失敗\n"
            "    → 檢查: 表單送出邏輯、驗證碼填入時機、頁面跳轉偵測"
        )

    if not issues:
        print("  所有指標正常，無需修正！")
    else:
        for issue in issues:
            print(issue)
            print()

    print(f"{'='*70}")


if __name__ == "__main__":
    asyncio.run(main())
