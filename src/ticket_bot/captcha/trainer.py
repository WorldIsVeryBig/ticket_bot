"""驗證碼訓練工具 — 標註收集的圖片 + 訓練自訂 ONNX 模型

使用流程：
1. 在 config.yaml 設定 captcha.collect_dir 啟用收集
2. 跑 ticket-bot run/watch 幾次，自動收集驗證碼圖片
3. ticket-bot label  — 人工標註圖片（顯示圖片，輸入正確文字）
4. ticket-bot train  — 用標註資料訓練自訂模型
"""

from __future__ import annotations

import json
import logging
import shutil
from pathlib import Path

logger = logging.getLogger(__name__)

LABELS_FILE = "labels.json"


def label_images(collect_dir: str, output_dir: str = ""):
    """互動式標註驗證碼圖片

    Args:
        collect_dir: 收集的驗證碼圖片目錄
        output_dir: 標註後的輸出目錄（預設 collect_dir/labeled）
    """
    collect_path = Path(collect_dir)
    if not collect_path.exists():
        print(f"目錄不存在: {collect_dir}")
        return

    output_path = Path(output_dir) if output_dir else collect_path / "labeled"
    output_path.mkdir(parents=True, exist_ok=True)

    labels_file = output_path / LABELS_FILE
    labels: dict[str, str] = {}
    if labels_file.exists():
        labels = json.loads(labels_file.read_text())
        print(f"已載入 {len(labels)} 筆標註")

    # 找所有未標註的圖片
    images = sorted(collect_path.glob("*.png"))
    unlabeled = [img for img in images if img.name not in labels]
    print(f"共 {len(images)} 張圖片，{len(unlabeled)} 張未標註\n")

    if not unlabeled:
        print("所有圖片都已標註！")
        _show_stats(labels)
        return

    # 嘗試用 PIL 開啟圖片顯示（macOS Preview / 其他系統的圖片檢視器）
    try:
        from PIL import Image
        has_pil = True
    except ImportError:
        has_pil = False
        print("提示：安裝 Pillow (pip install Pillow) 可自動顯示圖片\n")

    labeled_count = 0
    for i, img_path in enumerate(unlabeled):
        print(f"[{i + 1}/{len(unlabeled)}] {img_path.name}")

        # 從檔名提取 OCR 的猜測結果
        name_parts = img_path.stem.split("_")
        ocr_guess = name_parts[-1] if len(name_parts) >= 4 else ""
        conf_str = ""
        for part in name_parts:
            if part.startswith("conf"):
                conf_str = part

        if ocr_guess and ocr_guess != "unknown":
            print(f"  OCR 猜測: {ocr_guess} ({conf_str})")

        # 顯示圖片
        if has_pil:
            try:
                img = Image.open(img_path)
                img.show()
            except Exception:
                print(f"  圖片路徑: {img_path}")
        else:
            print(f"  圖片路徑: {img_path}")

        # 要求使用者輸入正確答案
        answer = input("  正確答案 (Enter=跳過, q=結束): ").strip()

        if answer.lower() == "q":
            break
        if not answer:
            continue

        # 儲存標註
        labels[img_path.name] = answer

        # 複製到 labeled 目錄，用答案重命名
        dest = output_path / f"{answer}_{img_path.name}"
        shutil.copy2(img_path, dest)

        labeled_count += 1
        print(f"  ✓ 已標註: {answer}\n")

    # 儲存標註檔
    labels_file.write_text(json.dumps(labels, ensure_ascii=False, indent=2))
    print(f"\n標註完成：新增 {labeled_count} 筆，總計 {len(labels)} 筆")
    print(f"標註檔: {labels_file}")
    _show_stats(labels)


def prepare_training_data(collect_dir: str, output_dir: str = ""):
    """將標註資料整理成 ddddocr-train 訓練格式

    輸出目錄結構：
    output_dir/
      train/
        image_001.png
        ...
      labels.csv  (filename,label)
      charset.txt
    """
    collect_path = Path(collect_dir)
    output_path = Path(output_dir) if output_dir else collect_path / "training_data"
    labeled_path = collect_path / "labeled"

    labels_file = labeled_path / LABELS_FILE
    if not labels_file.exists():
        print(f"找不到標註檔: {labels_file}")
        print("請先執行 ticket-bot label 標註圖片")
        return

    labels = json.loads(labels_file.read_text())
    if not labels:
        print("標註檔是空的")
        return

    # 建立輸出目錄
    train_dir = output_path / "train"
    train_dir.mkdir(parents=True, exist_ok=True)

    # 收集所有字元
    all_chars = set()
    csv_lines = ["filename,label"]

    for img_name, label in labels.items():
        src = collect_path / img_name
        if not src.exists():
            continue

        # 複製圖片
        dest = train_dir / img_name
        shutil.copy2(src, dest)
        csv_lines.append(f"{img_name},{label}")
        all_chars.update(label)

    # 寫入 labels.csv
    csv_file = output_path / "labels.csv"
    csv_file.write_text("\n".join(csv_lines))

    # 寫入 charset.txt
    charset = sorted(all_chars)
    charset_file = output_path / "charset.txt"
    charset_file.write_text("\n".join(charset))

    print(f"訓練資料已準備完成: {output_path}")
    print(f"  圖片數: {len(csv_lines) - 1}")
    print(f"  字元集 ({len(charset)}): {''.join(charset)}")
    print(f"  labels.csv: {csv_file}")
    print(f"  charset.txt: {charset_file}")
    print("\n下一步：用 ddddocr-train 訓練模型")
    print("  pip install ddddocr-train")
    print(f"  ddddocr-train --data {output_path} --output model/")


def _show_stats(labels: dict[str, str]):
    """顯示標註統計"""
    if not labels:
        return

    all_chars = set()
    lengths = {}
    for label in labels.values():
        all_chars.update(label)
        l = len(label)
        lengths[l] = lengths.get(l, 0) + 1

    print("\n📊 標註統計：")
    print(f"  總標註數: {len(labels)}")
    print(f"  字元集 ({len(all_chars)}): {''.join(sorted(all_chars))}")
    print(f"  長度分佈: {dict(sorted(lengths.items()))}")
