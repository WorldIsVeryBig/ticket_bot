FROM python:3.12-slim

# 設定環境變數
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    DEBIAN_FRONTEND=noninteractive \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    DISPLAY=:99

# 安裝系統相依套件與 Chromium
RUN apt-get update && apt-get install -y --no-install-recommends \
    chromium \
    chromium-driver \
    xvfb \
    fonts-noto-cjk \
    libgl1 \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# 設定工作目錄
WORKDIR /opt/ticket-bot

# 複製專案檔案 (使用 .dockerignore 排除多餘檔案)
COPY . /opt/ticket-bot/

# 安裝 Python 依賴
RUN pip install --upgrade pip && \
    pip install -e .

# 安裝 Playwright Chromium (以防 playwright engine 需要)
RUN playwright install chromium

# 複製啟動腳本
COPY scripts/deploy/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# 啟動命令
ENTRYPOINT ["/entrypoint.sh"]
CMD ["python", "-m", "ticket_bot", "bot", "-p", "telegram"]
