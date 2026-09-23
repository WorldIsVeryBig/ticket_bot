"""模擬站驗證碼收集 + 訓練 ONNX 模型 — 完整 pipeline

Usage:
    .venv/bin/python scripts/train/train_captcha.py collect --count 300
    .venv/bin/python scripts/train/train_captcha.py train --epochs 50
    .venv/bin/python scripts/train/train_captcha.py test
    .venv/bin/python scripts/train/train_captcha.py all          # 收集 + 訓練 + 測試
"""

import argparse
import asyncio
import csv
import json
import random
import time
from pathlib import Path

SITE = "https://www.ticket-training.com/plave_tp"
DATA_DIR = Path("captcha_training_data")
SAMPLES_DIR = DATA_DIR / "samples"
MODEL_DIR = Path("model")


# ═══════════════════════════════════════════════════════════════
#  Step 1: 從模擬站收集自動標註的驗證碼
# ═══════════════════════════════════════════════════════════════

async def collect(count: int):
    """用 Playwright 從模擬站批量收集已標註驗證碼"""
    from playwright.async_api import async_playwright

    SAMPLES_DIR.mkdir(parents=True, exist_ok=True)
    labels_file = DATA_DIR / "labels.json"
    labels: dict[str, str] = {}
    if labels_file.exists():
        labels = json.loads(labels_file.read_text())
        print(f"已有 {len(labels)} 筆標註")

    existing = len(labels)
    target = existing + count

    print(f"目標收集 {count} 張驗證碼 (已有 {existing}，共需 {target})")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()

        collected = 0
        errors = 0

        for i in range(count + 50):  # 多嘗試一些容忍錯誤
            if collected >= count:
                break

            try:
                # 進入 checking 頁面（每次都重新載入以取得新驗證碼）
                await page.goto(
                    f"{SITE}/checking?seat=特A區&price=6880&color=%23e29bb9",
                    wait_until="domcontentloaded",
                )
                await page.wait_for_timeout(300)

                # 取得驗證碼答案（從 data-answer 屬性）
                answer = await page.evaluate("""
                    () => {
                        const img = document.getElementById('captcha-image');
                        return img?.dataset?.answer || null;
                    }
                """)
                if not answer:
                    errors += 1
                    continue

                # 下載驗證碼圖片
                captcha_src = await page.evaluate("""
                    () => document.getElementById('captcha-image')?.src || null
                """)
                if not captcha_src:
                    errors += 1
                    continue

                resp = await page.request.get(captcha_src)
                if not resp.ok:
                    errors += 1
                    continue

                img_bytes = await resp.body()
                if len(img_bytes) < 100:
                    errors += 1
                    continue

                # 儲存
                filename = f"cap_{len(labels):04d}_{answer}.png"
                filepath = SAMPLES_DIR / filename
                filepath.write_bytes(img_bytes)
                labels[filename] = answer
                collected += 1

                if collected % 20 == 0:
                    print(f"  已收集 {collected}/{count} (錯誤: {errors})")

            except Exception as e:
                errors += 1
                if errors > 30:
                    print(f"錯誤太多 ({errors})，停止: {e}")
                    break

        await browser.close()

    # 儲存標註
    labels_file.parent.mkdir(parents=True, exist_ok=True)
    labels_file.write_text(json.dumps(labels, ensure_ascii=False, indent=2))
    print(f"\n收集完成: {collected} 張新圖片，總計 {len(labels)} 張")
    print(f"目錄: {SAMPLES_DIR}")

    # 統計
    all_chars = set()
    lengths = {}
    for label in labels.values():
        all_chars.update(label)
        lengths[len(label)] = lengths.get(len(label), 0) + 1
    print(f"字元集 ({len(all_chars)}): {''.join(sorted(all_chars))}")
    print(f"長度分佈: {dict(sorted(lengths.items()))}")


# ═══════════════════════════════════════════════════════════════
#  Step 2: 訓練 CRNN + CTC 模型
# ═══════════════════════════════════════════════════════════════

def preprocess_image(img_pil):
    """與 solver.py 完全一致的預處理邏輯"""
    import cv2
    import numpy as np
    from PIL import Image
    
    # PIL to OpenCV
    img_cv = cv2.cvtColor(np.array(img_pil), cv2.COLOR_RGB2BGR)
    gray = cv2.cvtColor(img_cv, cv2.COLOR_BGR2GRAY)
    
    # 1. 自適應二值化
    binary = cv2.adaptiveThreshold(
        gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 11, 2
    )
    
    # 2. 中值濾波去雜訊
    denoised = cv2.medianBlur(binary, 3)
    
    # 3. 形態學優化
    kernel = np.ones((2, 2), np.uint8)
    processed = cv2.morphologyEx(denoised, cv2.MORPH_OPEN, kernel)
    dilated = cv2.dilate(processed, kernel, iterations=1)
    
    # 4. 反轉回白底黑字 (與推論時一致)
    final = cv2.bitwise_not(dilated)
    
    return Image.fromarray(final)

def train(epochs: int = 50, batch_size: int = 32, lr: float = 1e-3):
    """用 CRNN + CTC loss 訓練驗證碼辨識模型"""
    import numpy as np
    import torch
    import torch.nn as nn
    from torch.utils.data import Dataset, DataLoader
    from PIL import Image
    import io

    # ── 載入資料（平衡權重）──
    mock_items: list[tuple[str, str]] = []
    tix_items: list[tuple[str, str]] = []
    syn_items: list[tuple[str, str]] = []

    # 來源 1: 模擬站資料
    labels_file = DATA_DIR / "labels.json"
    if labels_file.exists():
        labels = json.loads(labels_file.read_text())
        for fname, label in labels.items():
            fp = SAMPLES_DIR / fname
            if fp.exists():
                mock_items.append((str(fp), label))
    
    # 限制模擬站數據量，避免主導訓練
    random.shuffle(mock_items)
    mock_items = mock_items[:2000]
    print(f"模擬站資料: {len(mock_items)} 張 (已限額)")

    # 來源 2: 真實 tixcraft 資料
    tix_dir = DATA_DIR / "tixcraft_samples"
    tix_labels_file = tix_dir / "labels_auto.json"
    if tix_labels_file.exists():
        tix_labels = json.loads(tix_labels_file.read_text())
        for fname, label in tix_labels.items():
            fp = tix_dir / fname
            if fp.exists():
                tix_items.append((str(fp), label))
    
    # 重要：對真實數據進行 15 倍過採樣
    print(f"真實拓元:   {len(tix_items)} 張 -> 過採樣至 {len(tix_items) * 15} 張")
    tix_items = tix_items * 15

    # 來源 3: 合成資料
    syn_dir = DATA_DIR / "synthetic"
    syn_labels_file = syn_dir / "labels.json"
    if syn_labels_file.exists():
        syn_labels = json.loads(syn_labels_file.read_text())
        for fname, label in syn_labels.items():
            fp = syn_dir / fname
            if fp.exists():
                syn_items.append((str(fp), label))
    print(f"合成資料:   {len(syn_items)} 張")

    all_items = mock_items + tix_items + syn_items
    random.shuffle(all_items)
    
    if not all_items:
        print("找不到任何標註資料")
        return

    print(f"總訓練樣本數 (含權重): {len(all_items)} 張")

    # 建立字元集
    all_labels = [label for _, label in all_items]
    all_chars = sorted(set("".join(all_labels)))
    char_to_idx = {c: i + 1 for i, c in enumerate(all_chars)}
    idx_to_char = {i + 1: c for i, c in enumerate(all_chars)}
    num_classes = len(all_chars) + 1

    charset_file = MODEL_DIR / "charset.json"
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    charset_dict = {str(i + 1): c for i, c in enumerate(all_chars)}
    charset_dict["0"] = "" 
    charset_file.write_text(json.dumps(charset_dict, ensure_ascii=False, indent=2))

    # ── Dataset with specific augmentation for binary images ──
    IMG_H, IMG_W = 64, 160

    def augment_image(img_arr: np.ndarray, training: bool = True) -> np.ndarray:
        if not training:
            return img_arr
        
        import cv2
        img_uint8 = (img_arr * 255).astype(np.uint8)

        # 隨機仿射變換 (旋轉、平移、錯切) - 對扭曲字體很重要
        if random.random() < 0.8:
            rows, cols = img_uint8.shape
            angle = random.uniform(-15, 15)
            scale = random.uniform(0.85, 1.15)
            shear = random.uniform(-8, 8)
            
            center = (cols / 2, rows / 2)
            M = cv2.getRotationMatrix2D(center, angle, scale)
            M[0, 1] += shear / 100.0
            
            img_uint8 = cv2.warpAffine(img_uint8, M, (cols, rows), borderValue=255)
            
        # 隨機侵蝕/膨脹 (模擬字體粗細差異)
        if random.random() < 0.4:
            kernel = np.ones((random.randint(1, 2), random.randint(1, 2)), np.uint8)
            if random.random() < 0.5:
                img_uint8 = cv2.erode(img_uint8, kernel, iterations=1)
            else:
                img_uint8 = cv2.dilate(img_uint8, kernel, iterations=1)

        img_arr = img_uint8.astype(np.float32) / 255.0
        return img_arr

    class CaptchaDataset(Dataset):
        def __init__(self, items, training=False):
            self.items = items
            self.training = training

        def __len__(self):
            return len(self.items)

        def __getitem__(self, idx):
            filepath, label = self.items[idx]
            try:
                img = Image.open(filepath).convert("RGB")
                # 關鍵：應用與實戰一致的預處理
                img = preprocess_image(img).convert("L")
                img = img.resize((IMG_W, IMG_H))
                
                img_arr = np.array(img, dtype=np.float32) / 255.0
                img_arr = augment_image(img_arr, self.training)
                img_tensor = torch.from_numpy(img_arr).unsqueeze(0)

                encoded = [char_to_idx[c] for c in label]
                return img_tensor, torch.tensor(encoded, dtype=torch.long), len(encoded)
            except Exception as e:
                # 容錯處理
                return torch.zeros((1, IMG_H, IMG_W)), torch.tensor([1, 1, 1, 1]), 4

    def collate_fn(batch):
        images, targets, lengths = zip(*batch)
        images = torch.stack(images)
        # flatten all targets into 1D for CTC
        target_concat = torch.cat(targets)
        lengths = torch.tensor(lengths, dtype=torch.long)
        return images, target_concat, lengths

    # 切分 train/val
    items = all_items
    random.shuffle(items)
    split = int(len(items) * 0.85)
    train_items = items[:split]
    val_items = items[split:]
    print(f"訓練集: {len(train_items)}, 驗證集: {len(val_items)}")

    train_loader = DataLoader(
        CaptchaDataset(train_items, training=True), batch_size=batch_size,
        shuffle=True, collate_fn=collate_fn, num_workers=0,
    )
    val_loader = DataLoader(
        CaptchaDataset(val_items, training=False), batch_size=batch_size,
        shuffle=False, collate_fn=collate_fn, num_workers=0,
    )

    # ── CRNN 模型 (with Dropout) ──
    class CRNN(nn.Module):
        def __init__(self, num_classes, img_h=64):
            super().__init__()
            # CNN backbone with dropout
            self.cnn = nn.Sequential(
                nn.Conv2d(1, 32, 3, padding=1), nn.BatchNorm2d(32), nn.ReLU(),
                nn.MaxPool2d(2, 2),  # -> 32x80
                nn.Dropout2d(0.1),
                nn.Conv2d(32, 64, 3, padding=1), nn.BatchNorm2d(64), nn.ReLU(),
                nn.MaxPool2d(2, 2),  # -> 16x40
                nn.Dropout2d(0.1),
                nn.Conv2d(64, 128, 3, padding=1), nn.BatchNorm2d(128), nn.ReLU(),
                nn.MaxPool2d(2, 2),  # -> 8x20
                nn.Dropout2d(0.2),
                nn.Conv2d(128, 256, 3, padding=1), nn.BatchNorm2d(256), nn.ReLU(),
                nn.MaxPool2d((2, 1)),  # -> 4x20
                nn.Dropout2d(0.2),
                nn.Conv2d(256, 256, 3, padding=1), nn.BatchNorm2d(256), nn.ReLU(),
                nn.MaxPool2d((2, 1)),  # -> 2x20
                nn.Conv2d(256, 256, (2, 1)), nn.BatchNorm2d(256), nn.ReLU(),
                # -> 1x20
            )
            # RNN with dropout
            self.rnn = nn.GRU(256, 128, num_layers=2, bidirectional=True,
                              batch_first=True, dropout=0.3)
            self.dropout = nn.Dropout(0.3)
            self.fc = nn.Linear(256, num_classes)

        def forward(self, x):
            # x: (B, 1, H, W)
            conv = self.cnn(x)          # (B, 256, 1, W')
            conv = conv.squeeze(2)      # (B, 256, W')
            conv = conv.permute(0, 2, 1)  # (B, W', 256)
            rnn_out, _ = self.rnn(conv)  # (B, W', 256)
            output = self.fc(self.dropout(rnn_out))  # (B, W', num_classes)
            return output.permute(1, 0, 2)  # (T, B, C) for CTC

    device = torch.device("mps" if torch.backends.mps.is_available() else "cpu")
    print(f"裝置: {device}")

    model = CRNN(num_classes).to(device)
    optimizer = torch.optim.Adam(model.parameters(), lr=lr, weight_decay=1e-4)
    # 使用 CosineAnnealingLR，讓學習率平滑下降
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=epochs, eta_min=1e-5)
    ctc_loss = nn.CTCLoss(blank=0, zero_infinity=True)

    # ── 解碼函式 ──
    def decode(preds):
        """CTC greedy decode"""
        _, max_indices = preds.max(2)  # (T, B)
        results = []
        for b in range(max_indices.size(1)):
            raw = max_indices[:, b].tolist()
            # collapse repeats + remove blanks
            chars = []
            prev = -1
            for idx in raw:
                if idx != 0 and idx != prev:
                    if idx in idx_to_char:
                        chars.append(idx_to_char[idx])
                prev = idx
            results.append("".join(chars))
        return results

    def calc_accuracy(loader):
        """計算整字正確率"""
        model.eval()
        correct = 0
        total = 0
        with torch.no_grad():
            for images, targets, lengths in loader:
                images = images.to(device)
                preds = model(images)
                decoded = decode(preds)

                # 還原 label
                offset = 0
                for i, l in enumerate(lengths):
                    label_indices = targets[offset:offset + l].tolist()
                    label_str = "".join(idx_to_char.get(idx, "?") for idx in label_indices)
                    if decoded[i] == label_str:
                        correct += 1
                    total += 1
                    offset += l
        return correct / total if total else 0

    # ── 訓練迴圈 ──
    best_val_acc = 0
    print(f"\n開始訓練 ({epochs} epochs)...\n")

    for epoch in range(1, epochs + 1):
        model.train()
        total_loss = 0
        batch_count = 0

        for images, targets, lengths in train_loader:
            images = images.to(device)
            targets = targets
            lengths = lengths

            preds = model(images)  # (T, B, C)
            T = preds.size(0)
            B = preds.size(1)
            input_lengths = torch.full((B,), T, dtype=torch.long)

            log_probs = preds.log_softmax(2)
            loss = ctc_loss(log_probs, targets, input_lengths, lengths)

            optimizer.zero_grad()
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 5.0)
            optimizer.step()

            total_loss += loss.item()
            batch_count += 1

        avg_loss = total_loss / max(batch_count, 1)
        scheduler.step()

        # 每 5 epoch 算一次驗證準確率
        if epoch % 5 == 0 or epoch == 1:
            train_acc = calc_accuracy(train_loader)
            val_acc = calc_accuracy(val_loader)

            marker = ""
            if val_acc > best_val_acc:
                best_val_acc = val_acc
                # 儲存最佳模型
                torch.save(model.state_dict(), MODEL_DIR / "best_model.pth")
                marker = " ★"

            print(
                f"  Epoch {epoch:3d}/{epochs}  "
                f"loss={avg_loss:.4f}  "
                f"train_acc={train_acc:.1%}  "
                f"val_acc={val_acc:.1%}{marker}"
            )
        else:
            print(f"  Epoch {epoch:3d}/{epochs}  loss={avg_loss:.4f}")

    print(f"\n訓練完成！最佳驗證準確率: {best_val_acc:.1%}")

    # ── 匯出 ONNX ──
    print("\n匯出 ONNX 模型...")
    model.load_state_dict(torch.load(MODEL_DIR / "best_model.pth", weights_only=True))
    model.eval()
    model.to("cpu")

    dummy_input = torch.randn(1, 1, IMG_H, IMG_W)
    onnx_path = MODEL_DIR / "captcha_model.onnx"
    torch.onnx.export(
        model, dummy_input, str(onnx_path),
        input_names=["input"],
        output_names=["output"],
        dynamic_axes={"input": {0: "batch"}, "output": {1: "batch"}},
        opset_version=11,
    )
    print(f"ONNX 模型已儲存: {onnx_path}")
    print(f"字元集: {charset_file}")

    # 儲存模型 metadata
    meta = {
        "num_classes": num_classes,
        "charset": all_chars,
        "img_h": IMG_H,
        "img_w": IMG_W,
        "best_val_acc": best_val_acc,
        "train_samples": len(train_items),
        "val_samples": len(val_items),
    }
    (MODEL_DIR / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2))
    print(f"\n設定方式：")
    print(f"  config.yaml → captcha.custom_model_path: \"{onnx_path}\"")
    print(f"  config.yaml → captcha.custom_charset_path: \"{charset_file}\"")


# ═══════════════════════════════════════════════════════════════
#  Step 3: 測試自訓練模型 vs ddddocr
# ═══════════════════════════════════════════════════════════════

def test(rounds: int = 30):
    """在模擬站上即時比較自訓練模型 vs ddddocr"""
    import numpy as np
    import onnxruntime as ort
    from PIL import Image
    import io

    # 載入自訓練模型
    onnx_path = MODEL_DIR / "captcha_model.onnx"
    meta_file = MODEL_DIR / "meta.json"
    if not onnx_path.exists() or not meta_file.exists():
        print("找不到訓練好的模型，請先執行 train")
        return

    meta = json.loads(meta_file.read_text())
    charset = meta["charset"]
    idx_to_char = {i + 1: c for i, c in enumerate(charset)}
    IMG_H, IMG_W = meta["img_h"], meta["img_w"]

    session = ort.InferenceSession(str(onnx_path))
    input_name = session.get_inputs()[0].name

    def custom_predict(img_bytes: bytes) -> str:
        img_p = Image.open(io.BytesIO(img_bytes)).convert("RGB")
        # 關鍵：這裡也要用一樣的預處理
        img_p = preprocess_image(img_p).convert("L").resize((IMG_W, IMG_H))
        
        arr = np.array(img_p, dtype=np.float32) / 255.0
        tensor = arr[np.newaxis, np.newaxis, :, :]  # (1, 1, H, W)
        output = session.run(None, {input_name: tensor})[0]  # (T, 1, C)
        # greedy decode
        indices = output[:, 0, :].argmax(axis=1)
        chars = []
        prev = -1
        for idx in indices:
            if idx != 0 and idx != prev and idx in idx_to_char:
                chars.append(idx_to_char[idx])
            prev = idx
        return "".join(chars)

    # 載入 ddddocr
    import ddddocr
    ocr = ddddocr.DdddOcr(beta=True)

    def ddddocr_predict(img_bytes: bytes) -> tuple[str, float]:
        result = ocr.classification(img_bytes, probability=True)
        return result["text"], result["confidence"]

    # 跑即時測試
    async def _run():
        from playwright.async_api import async_playwright

        custom_correct = 0
        ddddocr_correct = 0
        custom_results = []
        ddddocr_results = []

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()

            print(f"{'='*75}")
            print(f"  自訓練模型 vs ddddocr — {rounds} 輪即時測試")
            print(f"{'='*75}\n")

            for i in range(1, rounds + 1):
                await page.goto(
                    f"{SITE}/checking?seat=特A區&price=6880&color=%23e29bb9",
                    wait_until="domcontentloaded",
                )
                await page.wait_for_timeout(300)

                answer = await page.evaluate("""
                    () => document.getElementById('captcha-image')?.dataset?.answer || null
                """)
                captcha_src = await page.evaluate("""
                    () => document.getElementById('captcha-image')?.src || null
                """)
                if not answer or not captcha_src:
                    continue

                resp = await page.request.get(captcha_src)
                img_bytes = await resp.body()

                # 自訓練模型
                custom_text = custom_predict(img_bytes)
                custom_ok = custom_text.lower() == answer.lower()
                if custom_ok:
                    custom_correct += 1
                custom_results.append(custom_ok)

                # ddddocr
                ddddocr_text, ddddocr_conf = ddddocr_predict(img_bytes)
                ddddocr_ok = ddddocr_text.lower() == answer.lower()
                if ddddocr_ok:
                    ddddocr_correct += 1
                ddddocr_results.append(ddddocr_ok)

                c_mark = "✓" if custom_ok else "✗"
                d_mark = "✓" if ddddocr_ok else "✗"
                print(
                    f"  [{i:2d}] 答案={answer:6s} "
                    f"| 自訓練: {c_mark} '{custom_text:6s}' "
                    f"| ddddocr: {d_mark} '{ddddocr_text:6s}' (conf={ddddocr_conf:.2f})"
                )

            await browser.close()

        # 統計
        total = len(custom_results)
        print(f"\n{'='*75}")
        print(f"  結果比較 ({total} 輪)")
        print(f"{'='*75}\n")
        print(f"  自訓練模型: {custom_correct}/{total} ({custom_correct/total*100:.1f}%)")
        print(f"  ddddocr:    {ddddocr_correct}/{total} ({ddddocr_correct/total*100:.1f}%)")
        diff = custom_correct - ddddocr_correct
        if diff > 0:
            print(f"\n  ✅ 自訓練模型勝出 (+{diff} 題)")
        elif diff < 0:
            print(f"\n  📊 ddddocr 仍較好 (+{-diff} 題)")
        else:
            print(f"\n  📊 兩者持平")

    asyncio.run(_run())


# ═══════════════════════════════════════════════════════════════
#  CLI
# ═══════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="模擬站驗證碼訓練 pipeline")
    sub = parser.add_subparsers(dest="cmd")

    p_collect = sub.add_parser("collect", help="從模擬站收集驗證碼")
    p_collect.add_argument("--count", "-n", type=int, default=300)

    p_train = sub.add_parser("train", help="訓練 CRNN + CTC 模型")
    p_train.add_argument("--epochs", "-e", type=int, default=50)
    p_train.add_argument("--batch-size", "-b", type=int, default=32)
    p_train.add_argument("--lr", type=float, default=1e-3)

    p_test = sub.add_parser("test", help="即時比較自訓練模型 vs ddddocr")
    p_test.add_argument("--rounds", "-n", type=int, default=30)

    p_all = sub.add_parser("all", help="完整 pipeline: 收集 + 訓練 + 測試")
    p_all.add_argument("--count", "-n", type=int, default=300)
    p_all.add_argument("--epochs", "-e", type=int, default=50)

    args = parser.parse_args()

    if args.cmd == "collect":
        asyncio.run(collect(args.count))
    elif args.cmd == "train":
        train(args.epochs, args.batch_size, args.lr)
    elif args.cmd == "test":
        test(args.rounds)
    elif args.cmd == "all":
        print("=" * 60)
        print("  Phase 1: 收集驗證碼")
        print("=" * 60)
        asyncio.run(collect(args.count))
        print("\n" + "=" * 60)
        print("  Phase 2: 訓練模型")
        print("=" * 60)
        train(args.epochs)
        print("\n" + "=" * 60)
        print("  Phase 3: 即時測試")
        print("=" * 60)
        test()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
