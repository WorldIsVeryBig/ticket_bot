import os
import json
import subprocess
from pathlib import Path

def label_images():
    sample_dir = Path("captcha_samples")
    labels_file = sample_dir / "labels.json"
    
    # 載入現有標註
    labels = {}
    if labels_file.exists():
        try:
            labels = json.loads(labels_file.read_text())
        except:
            labels = {}

    # 找出尚未標註的圖片
    images = [f for f in os.listdir(sample_dir) if f.endswith(".png") and f not in labels]
    images.sort()

    print(f"找到 {len(images)} 張待標註圖片。")
    print("操作說明: 輸入 4 位字母後按 Enter。若圖片看不清可直接按 Enter 跳過。按 Ctrl+C 結束。")
    print("-" * 30)

    try:
        for img_name in images:
            img_path = sample_dir / img_name
            
            # 使用 macOS 的 open 指令開啟圖片
            subprocess.run(["open", str(img_path)])
            
            ans = input(f"請輸入 [{img_name}] 的驗證碼: ").strip().lower()
            
            if len(ans) == 4:
                labels[img_name] = ans
                # 每輸入一筆就存檔一次，避免中斷
                labels_file.write_text(json.dumps(labels, ensure_ascii=False, indent=2))
            else:
                print("跳過...")
                
    except KeyboardInterrupt:
        print("\n已停止標註。")
    finally:
        print(f"\n標註完成！目前總計標註: {len(labels)} 張。")

if __name__ == "__main__":
    label_images()
