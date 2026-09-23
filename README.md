# Ticket Bot

以 Python 開發的票務流程自動化教學專案，整合命令列操作、瀏覽器自動化、活動條件篩選及票務狀態監測。

本專案主要供課程示範、程式設計練習，以及經授權的測試環境使用。請勿用於違反網站服務條款、活動規則或所在地法規的用途。

## 功能簡介

- 支援 Tixcraft、KKTIX 與 Ticket Plus 購票流程。
- 支援 Ticketmaster 活動監測。
- 提供 NoDriver 與 Playwright 兩種瀏覽器引擎。
- 可依活動名稱、日期、區域及票數篩選目標。
- 提供 `run` 一次執行與 `watch` 持續監測兩種模式。
- 支援開賣時間倒數、多個瀏覽器 Session、Telegram／Discord 通知。
- 包含驗證碼辨識、資料收集與訓練工具。

> 各平台頁面結構不同，實際可用功能以對應平台模組的實作為準。登入、OTP、人機驗證及付款等步驟可能仍需人工完成。

## 系統需求

- Python 3.11 以上
- Chrome 或 Chromium
- macOS 或 Linux
- Git

本專案目前的 Python 套件版本定義在 `pyproject.toml`。

## 安裝

### 1. 下載專案

```bash
git clone git@github.com:WorldIsVeryBig/ticket_bot.git
cd ticket-bot-public
```

### 2. 建立虛擬環境

macOS／Linux：

```bash
python3 -m venv .venv
source .venv/bin/activate
```

### 3. 安裝專案

```bash
python -m pip install --upgrade pip
python -m pip install -e .
```

如需執行測試或使用開發工具，可安裝開發依賴：

```bash
python -m pip install -e '.[dev]'
```

若設定使用 Playwright，還需要安裝 Chromium：

```bash
playwright install chromium
```

### 4. 確認安裝結果

```bash
ticket-bot --help
```

若終端機找不到 `ticket-bot`，請先確認虛擬環境已啟用：

```bash
source .venv/bin/activate
```

也可以改用模組方式執行：

```bash
python -m ticket_bot --help
```

## 建立設定檔

本機執行可先複製本機範例：

```bash
cp config.local.example.yaml config.yaml
```

主要設定格式如下：

```yaml
deployment:
  profile: local_desktop

events:
  - name: "課程測試活動"
    platform: ticketplus
    url: "https://你的授權測試網站/activity/活動識別碼"
    ticket_count: 1
    date_keyword: ""
    area_keyword: ""
    area_keywords: []
    max_price: null
    sale_time: ""
    presale_code: ""

browser:
  engine: nodriver
  headless: false
  user_data_dir: "./chrome_profile"
  pre_warm: true
  lang: "zh-TW"
  executable_path: ""
  api_mode: "off"
  turbo_mode: false

captcha:
  engine: ddddocr
  beta_model: true
  char_ranges: 0
  confidence_threshold: 0.6
  max_attempts: 5
  preprocess: false
  custom_model_path: ""
  custom_charset_path: ""
  collect_dir: ""

notifications:
  telegram:
    enabled: false
  discord:
    enabled: false

proxy:
  enabled: false
  rotate: true
  servers: []

trace:
  enabled: false
  log_path: "./logs/tixcraft_trace_local.jsonl"
```

### 活動設定欄位

| 欄位 | 說明 |
| --- | --- |
| `name` | 活動顯示名稱，也是 `--event` 搜尋使用的名稱 |
| `platform` | `tixcraft`、`kktix` 或 `ticketplus` |
| `url` | 活動頁面網址 |
| `ticket_count` | 希望選擇的票數 |
| `date_keyword` | 場次日期關鍵字；留空時由平台流程選擇可用場次 |
| `area_keyword` | 主要票區關鍵字 |
| `area_keywords` | Ticket Plus 可依序比對的多個票區關鍵字 |
| `max_price` | 可接受的最高票價；`null` 表示不設定 |
| `sale_time` | 倒數模式使用的開賣時間 |
| `presale_code` | 活動需要預售代碼時使用 |

### 瀏覽器設定欄位

| 欄位 | 說明 |
| --- | --- |
| `engine` | `nodriver` 或 `playwright` |
| `headless` | 是否隱藏瀏覽器視窗；第一次登入建議設為 `false` |
| `user_data_dir` | 儲存瀏覽器登入狀態的目錄 |
| `executable_path` | Chrome／Chromium 執行檔路徑；留空時由引擎自動尋找 |
| `api_mode` | Tixcraft 使用的流程模式；初次使用建議設為 `off` |
| `turbo_mode` | 是否啟用較快速的欄位操作方式 |

## 基本使用流程

以下指令都假設設定檔位於專案根目錄的 `config.yaml`。

### 1. 登入

依設定檔第一個活動的平台開啟登入頁：

```bash
ticket-bot --config config.yaml login
```

也可以明確指定平台：

```bash
ticket-bot --config config.yaml login --platform tixcraft
ticket-bot --config config.yaml login --platform kktix
ticket-bot --config config.yaml login --platform ticketplus
```

瀏覽器開啟後，請手動完成登入，再回到終端機按 Enter。登入資訊會保存在 `browser.user_data_dir` 指定的目錄。

### 2. 執行指定活動

```bash
ticket-bot --config config.yaml run --event "課程測試活動"
```

`--event` 採部分文字比對。若設定檔只有一個活動，也可以直接執行：

```bash
ticket-bot --config config.yaml run
```

可從命令列暫時覆蓋日期、區域與票數：

```bash
ticket-bot --config config.yaml run \
  --event "課程測試活動" \
  --date "2026-11-22" \
  --area "一般區" \
  --count 2
```

### 3. 持續監測

```bash
ticket-bot --config config.yaml watch \
  --event "課程測試活動" \
  --interval 5
```

程式會依指定秒數重新檢查；按 `Ctrl+C` 可停止。

### 4. 倒數啟動

先在活動設定填入 `sale_time`，再執行：

```bash
ticket-bot --config config.yaml countdown --event "課程測試活動"
```

倒數模式目前以 Tixcraft 活動為主。

### 5. 其他指令

```bash
# 列出 Tixcraft 可用場次
ticket-bot --config config.yaml list

# 啟動 Telegram 與 Discord Bot
ticket-bot --config config.yaml bot

# 監測 Ticketmaster 活動
ticket-bot --config config.yaml monitor "活動關鍵字"

# 查看所有主指令
ticket-bot --help

# 查看單一指令參數
ticket-bot run --help
ticket-bot watch --help
```

## 專案結構

```text
ticket-bot-public/
├── pyproject.toml
├── config.local.example.yaml
├── config.cloud.example.yaml
├── config.aws-tokyo.example.yaml
├── capture/
│   └── recorder.py
├── scripts/
│   ├── debug/
│   ├── diagnostics/
│   ├── release/
│   └── train/
├── src/ticket_bot/
│   ├── cli.py
│   ├── config.py
│   ├── telegram_bot.py
│   ├── discord_bot.py
│   ├── gemma_client.py
│   ├── network_trace.py
│   ├── browser/
│   ├── captcha/
│   ├── notifications/
│   ├── platforms/
│   ├── proxy/
│   ├── rl/
│   └── utils/
└── tests/
```

### 主要模組

| 路徑 | 用途 |
| --- | --- |
| `src/ticket_bot/cli.py` | Click 命令列入口與各命令流程 |
| `src/ticket_bot/config.py` | 載入 YAML、`.env` 與部署環境設定 |
| `src/ticket_bot/browser/` | NoDriver／Playwright 瀏覽器抽象層 |
| `src/ticket_bot/platforms/` | 各票務平台的流程與頁面解析器 |
| `src/ticket_bot/captcha/` | 驗證碼辨識與訓練資料處理 |
| `src/ticket_bot/notifications/` | Telegram／Discord 通知 |
| `src/ticket_bot/proxy/` | Proxy 選擇與輪替 |
| `src/ticket_bot/rl/` | 自適應重試與策略實驗模組 |
| `src/ticket_bot/utils/` | 重試、時間同步等共用工具 |
| `scripts/` | 除錯、診斷、訓練與匯出腳本 |
| `tests/` | 單元測試與流程測試 |

平台實作位於：

```text
src/ticket_bot/platforms/
├── tixcraft.py
├── tixcraft_api.py
├── tixcraft_parser.py
├── kktix.py
├── kktix_parser.py
├── ticketplus.py
├── ticketplus_parser.py
└── ticketmaster.py
```

## 常見問題

### 找不到 `config.yaml`

錯誤訊息：

```text
FileNotFoundError: 找不到設定檔：config.yaml
```

請在專案根目錄建立設定檔：

```bash
cp config.local.example.yaml config.yaml
```

或使用 `--config` 指定正確位置：

```bash
ticket-bot --config /完整路徑/config.yaml run
```

### 找不到 `ticket-bot` 指令

先啟用虛擬環境並重新安裝：

```bash
source .venv/bin/activate
python -m pip install -e .
```

### Chrome 無法啟動

先確認 Chrome for Testing、Google Chrome 或 Chromium 可以正常開啟。若程式無法自動找到瀏覽器，請在 `config.yaml` 設定完整路徑：

```yaml
browser:
  executable_path: "/完整路徑/Google Chrome for Testing"
```

macOS 的執行檔通常位於 `.app/Contents/MacOS/` 之下，而不是 `.app` 目錄本身。

### 瀏覽器顯示尚未登入

確認 `login` 與 `run` 使用同一份設定檔及相同的 `user_data_dir`：

```bash
ticket-bot --config config.yaml login --platform ticketplus
ticket-bot --config config.yaml run --event "課程測試活動"
```

登入完成後，請先確認瀏覽器已進入會員頁面，再回到終端機按 Enter。

### 活動或票區沒有被選中

依序確認：

1. `--event` 是否能部分比對到 `events[].name`。
2. `date_keyword` 是否和頁面顯示文字一致。
3. `area_keyword`／`area_keywords` 是否和票區名稱一致。
4. `ticket_count` 是否超過可購買數量。
5. `max_price` 是否把所有可用票種排除。

可以加入 `--verbose` 查看較完整的執行紀錄：

```bash
ticket-bot --verbose --config config.yaml run --event "課程測試活動"
```

全域參數必須放在子命令前面，因此是 `ticket-bot --verbose ... run`，不是 `ticket-bot run --verbose`。

### `watch` 一直重試但沒有成功

`watch` 只會根據頁面狀態持續檢查，不代表一定有符合條件的票。請檢查終端機中的錯誤訊息，並確認活動仍可購買、登入狀態有效、篩選條件正確。

### Playwright 尚未安裝瀏覽器

若看到找不到 Chromium 執行檔的訊息，執行：

```bash
playwright install chromium
```

或把 `browser.engine` 改回 `nodriver`。

## 使用提醒

本專案無法保證取得票券，也不應取代使用者確認活動規則、訂單資料及付款內容。執行任何自動化流程前，請確認已取得測試或操作授權。
