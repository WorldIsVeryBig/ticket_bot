import asyncio
import json
import io
import httpx
import numpy as np
import onnxruntime as ort
from pathlib import Path
from PIL import Image
import cv2
import time
from datetime import datetime

# 配置
MODEL_PATH = "model/captcha_model.onnx"
CHARSET_PATH = "model/charset.json"
DEBUG_DIR = Path("live_debug")
DEBUG_DIR.mkdir(exist_ok=True)

def preprocess_image(img_pil: Image.Image) -> Image.Image:
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

async def run_live_test(count=10):
    # 載入模型
    if not Path(MODEL_PATH).exists():
        print(f"錯誤: 找不到模型檔 {MODEL_PATH}")
        return

    session = ort.InferenceSession(MODEL_PATH)
    charset = json.loads(Path(CHARSET_PATH).read_text())
    idx_to_char = {int(k): v for k, v in charset.items()}
    input_name = session.get_inputs()[0].name

    print(f"🚀 開始拓元實戰測試 (預計抓取 {count} 張)...")
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Referer": "https://tixcraft.com/",
        "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
    }

    async with httpx.AsyncClient(http2=True, headers=headers, timeout=10) as client:
        for i in range(1, count + 1):
            try:
                # 1. 取得驗證碼 URL (模擬 tixcraft refresh captcha)
                resp = await client.get("https://tixcraft.com/ticket/captcha", params={"refresh": "1"})
                if resp.status_code != 200:
                    print(f"  [{i:2d}] 獲取失敗: HTTP {resp.status_code}")
                    continue
                
                img_url = ""
                if "json" in resp.headers.get("content-type", ""):
                    img_url = resp.json().get("url", "")
                
                if not img_url:
                    img_bytes = resp.content
                else:
                    img_resp = await client.get(f"https://tixcraft.com{img_url}")
                    img_bytes = img_resp.content

                if len(img_bytes) < 100:
                    print(f"  [{i:2d}] 圖片數據無效")
                    continue

                # 2. 模型辨識
                img_pil = Image.open(io.BytesIO(img_bytes)).convert("RGB")
                processed_pil = preprocess_image(img_pil).convert("L").resize((160, 64))
                arr = np.array(processed_pil, dtype=np.float32) / 255.0
                tensor = arr[np.newaxis, np.newaxis, :, :]
                
                output = session.run(None, {input_name: tensor})[0]
                indices = output[:, 0, :].argmax(axis=1)
                
                chars = []
                prev = -1
                for idx in indices:
                    if idx != 0 and idx != prev:
                        char = idx_to_char.get(idx, "")
                        if char: chars.append(char)
                    prev = idx
                prediction = "".join(chars)

                # 3. 儲存結果
                ts = datetime.now().strftime("%H%M%S_%f")
                raw_path = DEBUG_DIR / f"live_{ts}_pred_{prediction}_raw.png"
                proc_path = DEBUG_DIR / f"live_{ts}_pred_{prediction}_proc.png"
                
                raw_path.write_bytes(img_bytes)
                processed_pil.save(proc_path)
                
                print(f"  [{i:2d}] 辨識結果: {prediction:6s} | 已儲存至 {raw_path.name}")
                
                # 稍微延遲避免被鎖 IP
                await asyncio.sleep(1.5)

            except Exception as e:
                print(f"  [{i:2d}] 發生錯誤: {e}")

    print(f"\n✅ 測試完成！請檢查 {DEBUG_DIR} 目錄中的圖片。")

if __name__ == "__main__":
    asyncio.run(run_live_test())
