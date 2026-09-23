"""批量標註驗證碼 — 生成 HTML 頁面，在瀏覽器中快速標註

Usage:
    .venv/bin/python scripts/train/label_batch.py
    .venv/bin/python scripts/train/label_batch.py --dir captcha_training_data/tixcraft_samples --per-page 50

會在本機開一個 HTTP server，瀏覽器打開後：
  - 每頁顯示 N 張 captcha 圖片
  - 旁邊有輸入框，打完按 Tab 跳下一張
  - 按「儲存」寫入 labels.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs
import webbrowser

LABELS_FILE = "labels.json"


def generate_html(images: list[Path], labels: dict, page: int, total_pages: int, per_page: int) -> str:
    rows = []
    for i, img in enumerate(images):
        idx = (page - 1) * per_page + i + 1
        existing = labels.get(img.name, "")
        # 從檔名提取 OCR 猜測
        parts = img.stem.split("_")
        guess = parts[-1] if len(parts) >= 4 and parts[-1] != "unknown" else ""
        rows.append(f"""
        <tr>
          <td class="idx">{idx}</td>
          <td><img src="/img/{img.name}" loading="lazy"></td>
          <td class="guess">{guess}</td>
          <td><input type="text" name="{img.name}" value="{existing}"
               maxlength="6" autocomplete="off" spellcheck="false"
               pattern="[a-zA-Z0-9]{{4}}" placeholder="4字母"></td>
        </tr>""")

    # 分頁連結
    pages_html = ""
    for p in range(1, total_pages + 1):
        cls = ' class="active"' if p == page else ""
        pages_html += f' <a href="/?page={p}"{cls}>{p}</a>'

    return f"""<!DOCTYPE html>
<html><head>
<meta charset="utf-8">
<title>Captcha 標註 (第 {page}/{total_pages} 頁)</title>
<style>
body {{ font-family: -apple-system, sans-serif; max-width: 900px; margin: 20px auto; background: #1a1a1a; color: #eee; }}
h1 {{ font-size: 18px; }}
table {{ border-collapse: collapse; width: 100%; }}
tr:hover {{ background: #2a2a2a; }}
td {{ padding: 4px 8px; vertical-align: middle; }}
td.idx {{ color: #666; width: 40px; text-align: right; }}
td.guess {{ color: #888; font-family: monospace; width: 60px; }}
img {{ height: 50px; image-rendering: pixelated; background: white; border-radius: 3px; }}
input[type="text"] {{ font-size: 16px; font-family: monospace; width: 80px; padding: 4px 8px;
  background: #333; color: #0f0; border: 1px solid #555; border-radius: 3px; text-transform: lowercase; }}
input:focus {{ border-color: #0af; outline: none; }}
input.done {{ border-color: #0a0; }}
.bar {{ display: flex; justify-content: space-between; align-items: center; margin: 10px 0; }}
button {{ font-size: 16px; padding: 8px 24px; background: #0a0; color: #fff; border: none; border-radius: 5px; cursor: pointer; }}
button:hover {{ background: #0c0; }}
.pages a {{ color: #aaa; margin: 0 4px; text-decoration: none; }}
.pages a.active {{ color: #0f0; font-weight: bold; }}
.stats {{ color: #888; font-size: 14px; }}
</style>
</head><body>
<h1>Captcha 批量標註 — 第 {page}/{total_pages} 頁</h1>
<div class="bar">
  <span class="stats" id="stats">已填: 0/{len(images)}</span>
  <div class="pages">{pages_html}</div>
  <button onclick="save()">💾 儲存</button>
</div>
<form id="form">
<table>
<tr><th></th><th>圖片</th><th>猜測</th><th>答案</th></tr>
{"".join(rows)}
</table>
</form>
<div class="bar" style="margin-top:10px;">
  <span id="msg"></span>
  <button onclick="save()">💾 儲存</button>
</div>
<script>
const inputs = document.querySelectorAll('input[type="text"]');
function updateStats() {{
  let filled = 0;
  inputs.forEach(inp => {{ if(inp.value.trim()) filled++; inp.classList.toggle('done', inp.value.trim().length >= 4); }});
  document.getElementById('stats').textContent = '已填: ' + filled + '/{len(images)}';
}}
inputs.forEach(inp => {{
  inp.addEventListener('input', function() {{
    this.value = this.value.toLowerCase().replace(/[^a-z0-9]/g, '');
    updateStats();
    if(this.value.length >= 4) {{
      const next = this.closest('tr').nextElementSibling;
      if(next) next.querySelector('input').focus();
    }}
  }});
}});
updateStats();
// 自動 focus 第一個空的
for(const inp of inputs) {{ if(!inp.value.trim()) {{ inp.focus(); break; }} }}

async function save() {{
  const data = {{}};
  inputs.forEach(inp => {{ if(inp.value.trim()) data[inp.name] = inp.value.trim().toLowerCase(); }});
  const resp = await fetch('/save', {{
    method: 'POST',
    headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify(data),
  }});
  const result = await resp.json();
  document.getElementById('msg').textContent = result.message;
  document.getElementById('msg').style.color = '#0f0';
}}
</script>
</body></html>"""


class LabelHandler(SimpleHTTPRequestHandler):
    images_dir: Path
    all_images: list[Path]
    labels: dict
    labels_path: Path
    per_page: int

    def do_GET(self):
        if self.path.startswith("/img/"):
            # 提供圖片
            fname = self.path[5:]
            fpath = self.server.images_dir / fname
            if fpath.exists():
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                data = fpath.read_bytes()
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            else:
                self.send_error(404)
            return

        # 主頁
        page = 1
        if "page=" in self.path:
            try:
                page = int(self.path.split("page=")[1].split("&")[0])
            except ValueError:
                page = 1

        pp = self.server.per_page
        total = len(self.server.all_images)
        total_pages = max(1, (total + pp - 1) // pp)
        page = max(1, min(page, total_pages))
        start = (page - 1) * pp
        page_images = self.server.all_images[start:start + pp]

        html = generate_html(page_images, self.server.labels, page, total_pages, pp)
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        data = html.encode()
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        if self.path == "/save":
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            new_labels = json.loads(body)

            # 合併到現有 labels
            self.server.labels.update(new_labels)
            self.server.labels_path.write_text(
                json.dumps(self.server.labels, ensure_ascii=False, indent=2)
            )
            count = len(self.server.labels)
            resp = json.dumps({"message": f"已儲存！總計 {count} 筆標註"}).encode()

            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(resp)))
            self.end_headers()
            self.wfile.write(resp)
            return

        self.send_error(404)

    def log_message(self, format, *args):
        pass  # 靜音 HTTP log


def main():
    parser = argparse.ArgumentParser(description="批量標註 captcha")
    parser.add_argument("--dir", default="captcha_training_data/tixcraft_samples")
    parser.add_argument("--per-page", type=int, default=50)
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()

    images_dir = Path(args.dir)
    if not images_dir.exists():
        print(f"目錄不存在: {args.dir}")
        return

    # 載入已有標註
    labels_path = images_dir / LABELS_FILE
    labels = {}
    if labels_path.exists():
        labels = json.loads(labels_path.read_text())

    # 找所有圖片
    all_images = sorted(images_dir.glob("tix_*.png"))
    unlabeled = [img for img in all_images if img.name not in labels]

    print(f"圖片: {len(all_images)} 張 (已標註: {len(labels)}, 未標註: {len(unlabeled)})")
    print(f"每頁: {args.per_page} 張")
    print(f"瀏覽器打開: http://localhost:{args.port}")
    print("按 Ctrl+C 結束\n")

    server = HTTPServer(("127.0.0.1", args.port), LabelHandler)
    server.images_dir = images_dir
    server.all_images = all_images
    server.labels = labels
    server.labels_path = labels_path
    server.per_page = args.per_page

    webbrowser.open(f"http://localhost:{args.port}")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print(f"\n已儲存 {len(labels)} 筆標註到 {labels_path}")


if __name__ == "__main__":
    main()
