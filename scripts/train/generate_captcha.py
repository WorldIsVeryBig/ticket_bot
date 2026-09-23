"""從模擬站圖片做資料增強，擴充訓練資料

策略：模擬站的風格已經非常接近真實 tixcraft，
所以不從頭畫字，而是對既有模擬站圖片做增強變換來擴充資料量。

增強方式：
- 隨機亮度/對比度
- 高斯雜訊
- 微旋轉 + 平移
- 隨機 erosion/dilation（字體粗細變化）
- 隨機模糊
- 彈性扭曲

Usage:
    .venv/bin/python scripts/train/generate_captcha.py --count 5000
    .venv/bin/python scripts/train/generate_captcha.py --count 5000 --preview 10
"""

import argparse
import json
import random
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

DATA_DIR = Path("captcha_training_data")
SAMPLES_DIR = DATA_DIR / "samples"


def augment(img_arr: np.ndarray) -> np.ndarray:
    """對一張圖片套用隨機增強組合"""
    h, w = img_arr.shape[:2]

    # 轉成 float 方便處理
    is_color = len(img_arr.shape) == 3
    img = img_arr.astype(np.float32)
    if img.max() > 1.0:
        img = img / 255.0

    # 1. 隨機亮度 + 對比度（輕微）
    if random.random() < 0.5:
        brightness = random.uniform(-0.06, 0.06)
        contrast = random.uniform(0.9, 1.1)
        img = np.clip((img - 0.5) * contrast + 0.5 + brightness, 0, 1)

    # 2. 輕微高斯雜訊
    if random.random() < 0.25:
        sigma = random.uniform(0.005, 0.02)
        noise = np.random.normal(0, sigma, img.shape).astype(np.float32)
        img = np.clip(img + noise, 0, 1)

    img_uint8 = (img * 255).astype(np.uint8)

    # 3. 微旋轉 + 平移 + 縮放（輕微）
    if random.random() < 0.4:
        angle = random.uniform(-4, 4)
        scale = random.uniform(0.95, 1.05)
        tx, ty = random.uniform(-2, 2), random.uniform(-2, 2)
        center = (w / 2, h / 2)
        M = cv2.getRotationMatrix2D(center, angle, scale)
        M[0, 2] += tx
        M[1, 2] += ty
        img_uint8 = cv2.warpAffine(img_uint8, M, (w, h), borderMode=cv2.BORDER_REPLICATE)

    # 4. Erosion / Dilation（字體粗細）
    if random.random() < 0.3:
        kernel = np.ones((2, 2), np.uint8)
        if random.random() < 0.5:
            img_uint8 = cv2.erode(img_uint8, kernel, iterations=1)
        else:
            img_uint8 = cv2.dilate(img_uint8, kernel, iterations=1)

    # 5. 輕微模糊
    if random.random() < 0.25:
        ksize = random.choice([3, 3, 5])
        img_uint8 = cv2.GaussianBlur(img_uint8, (ksize, ksize), 0)

    # 6. 輕微彈性扭曲（模擬不同渲染引擎的微妙差異）
    if random.random() < 0.2:
        alpha = random.uniform(1, 3)
        sigma = random.uniform(3, 5)
        dx = cv2.GaussianBlur(
            (np.random.rand(h, w).astype(np.float32) * 2 - 1), (5, 5), sigma
        ) * alpha
        dy = cv2.GaussianBlur(
            (np.random.rand(h, w).astype(np.float32) * 2 - 1), (5, 5), sigma
        ) * alpha
        x, y = np.meshgrid(np.arange(w), np.arange(h))
        map_x = (x + dx).astype(np.float32)
        map_y = (y + dy).astype(np.float32)
        if is_color:
            img_uint8 = cv2.remap(img_uint8, map_x, map_y, cv2.INTER_LINEAR,
                                  borderMode=cv2.BORDER_REPLICATE)
        else:
            img_uint8 = cv2.remap(img_uint8, map_x, map_y, cv2.INTER_LINEAR,
                                  borderMode=cv2.BORDER_REPLICATE)

    return img_uint8


def main():
    parser = argparse.ArgumentParser(description="模擬站資料增強產生器")
    parser.add_argument("--count", "-n", type=int, default=5000, help="產生數量")
    parser.add_argument("--output", "-o", type=str, default="captcha_training_data/augmented",
                        help="輸出目錄")
    parser.add_argument("--preview", "-p", type=int, default=0,
                        help="預覽 N 張到 /tmp/captcha_preview/")
    args = parser.parse_args()

    # 載入模擬站原始資料
    labels_file = DATA_DIR / "labels.json"
    if not labels_file.exists():
        print("找不到模擬站標註資料，請先執行 scripts/train/train_captcha.py collect")
        return

    src_labels = json.loads(labels_file.read_text())
    src_items = [(f, l) for f, l in src_labels.items() if (SAMPLES_DIR / f).exists()]
    print(f"來源: {len(src_items)} 張模擬站圖片")

    if not src_items:
        print("沒有找到圖片檔案")
        return

    out_dir = Path(args.output)
    out_dir.mkdir(parents=True, exist_ok=True)

    # 預覽模式
    if args.preview > 0:
        preview_dir = Path("/tmp/captcha_preview")
        preview_dir.mkdir(parents=True, exist_ok=True)
        print(f"\n預覽 {args.preview} 張 → {preview_dir}/")
        for i in range(args.preview):
            fname, label = random.choice(src_items)
            img = cv2.imread(str(SAMPLES_DIR / fname))
            aug = augment(img)
            cv2.imwrite(str(preview_dir / f"aug_{i:03d}_{label}.png"), aug)
            # 也存一份原圖對比
            if i < 3:
                cv2.imwrite(str(preview_dir / f"orig_{i:03d}_{label}.png"), img)
            print(f"  aug_{i:03d}_{label}.png")
        print()

    # 批量增強
    labels = {}
    print(f"產生 {args.count} 張增強圖片 → {out_dir}/")

    for i in range(args.count):
        fname, label = random.choice(src_items)
        img = cv2.imread(str(SAMPLES_DIR / fname))
        aug = augment(img)

        out_name = f"aug_{i:05d}_{label}.png"
        cv2.imwrite(str(out_dir / out_name), aug)
        labels[out_name] = label

        if (i + 1) % 500 == 0:
            print(f"  {i + 1}/{args.count}")

    # 儲存標註
    labels_file = out_dir / "labels.json"
    labels_file.write_text(json.dumps(labels, ensure_ascii=False, indent=2))
    print(f"\n完成！{len(labels)} 張增強圖片")
    print(f"標註: {labels_file}")


if __name__ == "__main__":
    main()
