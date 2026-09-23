import asyncio
import json
import io
import numpy as np
import onnxruntime as ort
from pathlib import Path
from PIL import Image
import cv2
from playwright.async_api import async_playwright

# 配置
SITE = "https://ticket-training.onrender.com"
MODEL_PATH = "model/captcha_model.onnx"
CHARSET_PATH = "model/charset.json"
ERROR_DIR = Path("debug_failures")
ERROR_DIR.mkdir(exist_ok=True)

def preprocess_image(img_pil: Image.Image) -> Image.Image:
    # 與 solver.py / train_captcha.py 一致
    img_cv = cv2.cvtColor(np.array(img_pil), cv2.COLOR_RGB2BGR)
    gray = cv2.cvtColor(img_cv, cv2.COLOR_BGR2GRAY)
    binary = cv2.adaptiveThreshold(
        gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 11, 2
    )
    denoised = cv2.medianBlur(binary, 3)
    kernel = np.ones((2, 2), np.uint8)
    processed = cv2.morphologyEx(denoised, cv2.MORPH_OPEN, kernel)
    dilated = cv2.dilate(processed, kernel, iterations=1)
    final = cv2.bitwise_not(dilated)
    return Image.fromarray(final)

async def diagnose(rounds=50):
    # 載入模型
    session = ort.InferenceSession(MODEL_PATH)
    charset = json.loads(Path(CHARSET_PATH).read_text())
    idx_to_char = {int(k): v for k, v in charset.items()}
    input_name = session.get_inputs()[0].name

    results = []
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        
        print(f"開始執行 {rounds} 輪診斷測試...\n")
        
        for i in range(1, rounds + 1):
            await page.goto(f"{SITE}/checking?seat=A&price=100", wait_until="networkidle")
            
            # 取得答案與圖片
            answer = await page.evaluate("() => document.getElementById('captcha-image').dataset.answer")
            captcha_src = await page.evaluate("() => document.getElementById('captcha-image').src")
            
            resp = await page.request.get(captcha_src)
            img_bytes = await resp.body()
            
            # 模型預測
            img_pil = Image.open(io.BytesIO(img_bytes)).convert("RGB")
            processed_pil = preprocess_image(img_pil).convert("L").resize((160, 64))
            arr = np.array(processed_pil, dtype=np.float32) / 255.0
            tensor = arr[np.newaxis, np.newaxis, :, :]
            
            output = session.run(None, {input_name: tensor})[0]
            indices = output[:, 0, :].argmax(axis=1)
            
            # CTC Decode
            chars = []
            prev = -1
            for idx in indices:
                if idx != 0 and idx != prev:
                    char = idx_to_char.get(idx, "")
                    if char: chars.append(char)
                prev = idx
            prediction = "".join(chars)
            
            is_correct = prediction.lower() == answer.lower()
            
            if not is_correct:
                # 儲存錯誤圖片
                filename = f"fail_{i:02d}_ans_{answer}_pred_{prediction}.png"
                filepath = ERROR_DIR / filename
                filepath.write_bytes(img_bytes)
                
                # 同時儲存預處理後的圖片以便對比
                processed_filepath = ERROR_DIR / f"fail_{i:02d}_processed.png"
                processed_pil.save(processed_filepath)
                
                print(f"❌ [{i:2d}] 錯誤！答案: {answer} | 預測: {prediction} -> 已儲存到 {filename}")
                results.append({"round": i, "answer": answer, "pred": prediction, "img": str(filepath)})
            else:
                print(f"✅ [{i:2d}] 正確: {answer}")

        await browser.close()

    total_err = len(results)
    print(f"\n診斷結束。總錯誤數: {total_err}/{rounds} (準確率: {(rounds-total_err)/rounds:.1%})")
    return results

if __name__ == "__main__":
    asyncio.run(diagnose())
