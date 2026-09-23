import cv2
import numpy as np
from pathlib import Path
import io
from PIL import Image

def preprocess(image_bytes: bytes) -> bytes:
    nparr = np.frombuffer(image_bytes, np.uint8)
    img_cv = cv2.imdecode(nparr, cv2.IMREAD_GRAYSCALE)
    if img_cv is None:
        return image_bytes

    # 1. 自適應二值化
    binary = cv2.adaptiveThreshold(
        img_cv, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 11, 2
    )
    
    # 2. 中值濾波去雜訊
    denoised = cv2.medianBlur(binary, 3)
    
    # 3. 形態學優化
    kernel = np.ones((2, 2), np.uint8)
    processed = cv2.morphologyEx(denoised, cv2.MORPH_OPEN, kernel)
    dilated = cv2.dilate(processed, kernel, iterations=1)
    
    # 4. 反轉回白底黑字
    final = cv2.bitwise_not(dilated)
    
    _, buffer = cv2.imencode(".png", final)
    return buffer.tobytes()

def main():
    tix_dir = Path("captcha_training_data/tixcraft_samples")
    output_dir = Path("debug_output")
    output_dir.mkdir(exist_ok=True)
    
    samples = list(tix_dir.glob("*.png"))[:5]
    for i, p in enumerate(samples):
        print(f"處理: {p.name}")
        raw_bytes = p.read_bytes()
        processed_bytes = preprocess(raw_bytes)
        
        # 儲存對比圖
        raw_img = cv2.imread(str(p))
        processed_img = cv2.imdecode(np.frombuffer(processed_bytes, np.uint8), cv2.IMREAD_COLOR)
        
        # 調整大小以便對比
        h, w = 64, 160
        raw_img = cv2.resize(raw_img, (w, h))
        processed_img = cv2.resize(processed_img, (w, h))
        
        combined = np.hstack((raw_img, processed_img))
        cv2.imwrite(str(output_dir / f"compare_{i}.png"), combined)

if __name__ == "__main__":
    main()
