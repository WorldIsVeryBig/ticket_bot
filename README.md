# Ticket Bot

以 Python 開發的票務流程自動化教學專案，整合命令列操作、瀏覽器自動化、活動條件篩選、開賣倒數、釋票監測與驗證碼模型訓練。

本專案主要供課程示範、程式設計練習，以及經授權的測試環境使用。請勿用於違反網站服務條款、活動規則或所在地法規的用途。

> 登入、OTP、人機驗證與付款等步驟可能仍需人工完成。程式建立訂單後會停在付款前，請務必自行確認場次、票區、張數、金額與付款資料。

## 目錄

- [功能簡介](#功能簡介)
- [系統需求](#系統需求)
- [安裝](#安裝)
- [五分鐘快速開始](#五分鐘快速開始)
- [指令速查表](#指令速查表)
- [登入：login](#登入login)
- [執行一次：run](#執行一次run)
- [開賣倒數：countdown](#開賣倒數countdown)
- [持續監測釋票：watch](#持續監測釋票watch)
- [其他指令](#其他指令)
- [驗證碼收集與模型訓練](#驗證碼收集與模型訓練)
- [設定檔說明](#設定檔說明)
- [多帳號 Session](#多帳號-session)
- [專案結構](#專案結構)
- [常見問題](#常見問題)

## 功能簡介

- 支援 Tixcraft、KKTIX 與 Ticket Plus 購票流程。
- 支援 Ticketmaster 活動監測。
- 提供 NoDriver 與 Playwright 兩種瀏覽器引擎。
- 可依活動名稱、日期、票區、價格與票數篩選目標。
- 提供 `run`、`countdown` 與 `watch` 三種主要執行方式。
- 支援多個瀏覽器 Session、Telegram／Discord 通知。
- 支援驗證碼樣本收集、人工標註、資料整理與 ONNX 模型訓練。

各平台頁面結構不同，實際可用功能以對應平台模組的實作為準。

## 系統需求

- Python 3.11 以上
- Google Chrome、Chrome for Testing 或 Chromium
- macOS 或 Linux
- Git

Python 套件版本與命令列入口定義在 `pyproject.toml`。

## 安裝

### 1. 下載專案

使用 SSH：

```bash
git clone git@github.com:WorldIsVeryBig/ticket_bot.git
cd ticket_bot
```

若使用 HTTPS，GitHub 私人 Repository 必須使用 Personal Access Token，不能輸入 GitHub 帳號密碼。

### 2. 建立並啟用虛擬環境

```bash
python3 -m venv .venv
source .venv/bin/activate
```

之後每次重新開啟終端機，都需要在專案目錄再次執行：

```bash
source .venv/bin/activate
```

### 3. 安裝專案

```bash
python -m pip install --upgrade pip
python -m pip install -e .
```

如需執行測試：

```bash
python -m pip install -e '.[dev]'
```

如果使用 Playwright：

```bash
playwright install chromium
```

### 4. 確認安裝

```bash
ticket-bot --help
```

若找不到 `ticket-bot`，可以改用：

```bash
python -m ticket_bot --help
```

## 五分鐘快速開始

### 第一步：建立設定檔

```bash
cp config.local.example.yaml config.yaml
```

編輯 `config.yaml`，至少設定一個活動：

```yaml
events:
  - name: "Test"
    platform: ticketplus
    url: "https://ticketplus.com.tw/activity/活動識別碼"
    ticket_count: 2
    date_keyword: "2026-11-22"
    area_keywords:
      - "C"
      - "B"
    max_price: null
    sale_time: "2026-11-01T12:00:00+08:00"
    presale_code: ""

browser:
  engine: playwright
  headless: false
  user_data_dir: "./chrome_profile"
  pre_warm: true
  lang: "zh-TW"
  executable_path: ""
  api_mode: "off"
```

### 第二步：登入票務網站

```bash
ticket-bot --config config.yaml login --platform ticketplus
```

在瀏覽器完成登入後，回到終端機按 Enter。登入狀態會儲存在 `browser.user_data_dir` 指定的目錄。

### 第三步：選擇執行方式

立即執行一次：

```bash
ticket-bot --config config.yaml run --event "Test"
```

等待設定的開賣時間：

```bash
ticket-bot --config config.yaml countdown --event "Test"
```

持續監測釋出票券：

```bash
ticket-bot --config config.yaml watch --event "Test" --interval 5
```

## 指令速查表

| 想做的事 | 指令 |
| --- | --- |
| 查看全部指令 | `ticket-bot --help` |
| 查看單一指令參數 | `ticket-bot run --help` |
| 登入 Tixcraft | `ticket-bot --config config.yaml login --platform tixcraft` |
| 登入 KKTIX | `ticket-bot --config config.yaml login --platform kktix` |
| 登入 Ticket Plus | `ticket-bot --config config.yaml login --platform ticketplus` |
| 立即執行指定活動 | `ticket-bot --config config.yaml run --event "Test"` |
| 測試瀏覽器預熱 | `ticket-bot --config config.yaml run --event "Test" --dry-run` |
| 等待開賣時間 | `ticket-bot --config config.yaml countdown --event "Test"` |
| 持續監測釋票 | `ticket-bot --config config.yaml watch --event "Test" --interval 5` |
| 列出 Tixcraft 場次 | `ticket-bot --config config.yaml list` |
| 啟動通知 Bot | `ticket-bot --config config.yaml bot` |
| 監測 Ticketmaster | `ticket-bot --config config.yaml monitor "活動關鍵字"` |
| 標註驗證碼 | `ticket-bot --config config.yaml label --dir ./captcha_samples` |
| 整理訓練資料 | `ticket-bot --config config.yaml prepare --dir ./captcha_samples` |
| 顯示詳細日誌 | `ticket-bot --verbose --config config.yaml run --event "Test"` |

### 參數位置規則

`--config` 和 `--verbose` 是全域參數，必須放在 `run`、`watch` 等子命令前面。

正確：

```bash
ticket-bot --verbose --config config.yaml run --event "Test"
```

錯誤：

```bash
ticket-bot run --verbose --config config.yaml --event "Test"
```

## 登入：`login`

用途：開啟指定平台的登入頁面，讓使用者手動完成登入，並把登入狀態保存在瀏覽器 profile。

```bash
ticket-bot --config config.yaml login --platform ticketplus
```

可用平台：

```text
tixcraft
kktix
ticketplus
auto
```

使用 `auto` 或省略 `--platform` 時，程式會使用設定檔第一個活動的平台：

```bash
ticket-bot --config config.yaml login
```

登入與後續執行必須使用：

- 同一份 `config.yaml`
- 相同的 `browser.user_data_dir`
- 相同的瀏覽器引擎與 Chrome 執行檔

## 執行一次：`run`

用途：立即開啟活動頁並執行一次購票流程。

```bash
ticket-bot --config config.yaml run --event "Test"
```

`--event` 會對 `events[].name` 做部分文字比對。若設定檔只有一個支援活動，也可以省略：

```bash
ticket-bot --config config.yaml run
```

### 暫時覆蓋活動條件

以下參數只影響本次執行，不會修改 `config.yaml`：

```bash
ticket-bot --config config.yaml run \
  --event "Test" \
  --date "2026-11-22" \
  --area "B" \
  --count 2
```

| 參數 | 用途 |
| --- | --- |
| `--event` | 指定活動名稱，支援部分比對 |
| `--date` | 暫時覆蓋 `date_keyword` |
| `--area` | 暫時覆蓋票區關鍵字 |
| `--count` | 暫時覆蓋購票張數 |
| `--dry-run` | 只啟動與預熱瀏覽器，不進行購票 |
| `--parallel`、`-p` | 使用多個 Session 並行執行 |
| `--api` | Tixcraft 使用 API 流程；Ticket Plus 與 KKTIX 不使用此參數 |

## 開賣倒數：`countdown`

用途：等待活動的 `sale_time`，於開賣前預熱瀏覽器，倒數歸零後直接使用原本的瀏覽器執行活動流程。

支援：

- Tixcraft
- KKTIX
- Ticket Plus

先在活動設定填入帶有時區的 ISO 8601 時間：

```yaml
events:
  - name: "Test"
    platform: ticketplus
    sale_time: "2026-11-01T12:00:00+08:00"
```

再執行：

```bash
ticket-bot --config config.yaml countdown --event "Test"
```

倒數流程：

1. 距開賣超過 10 分鐘時先保持待機。
2. 開賣前 10 分鐘啟動瀏覽器。
3. 提前開啟活動頁並保留登入狀態。
4. 倒數歸零後沿用原本的瀏覽器與分頁執行。
5. 第一輪未成功時，依平台流程切換為釋票監測。

如果 `sale_time` 已經早於目前時間，程式會顯示「開賣時間已過」並立即執行，不會等待到下一天。

多 Session 並行：

```bash
ticket-bot --config config.yaml countdown --event "Test" --parallel
```

## 持續監測釋票：`watch`

用途：持續重新檢查活動狀態，發現符合條件的票券後執行購票流程。

```bash
ticket-bot --config config.yaml watch \
  --event "Test" \
  --interval 5
```

可以暫時覆蓋日期、票區與張數：

```bash
ticket-bot --config config.yaml watch \
  --event "Test" \
  --date "2026-11-22" \
  --area "B" \
  --count 2 \
  --interval 5
```

| 參數 | 用途 |
| --- | --- |
| `--interval` | 每次檢查之間的秒數，預設為 5 秒 |
| `--parallel`、`-p` | 多 Session 並行監測 |

按 `Ctrl+C` 可停止監測。`watch` 持續執行不代表一定有票，篩選條件不符或網站沒有釋票時會繼續等待。

## 其他指令

### 列出 Tixcraft 場次

```bash
ticket-bot --config config.yaml list
```

`list` 目前只會讀取設定檔中的 Tixcraft 活動。

### 啟動 Telegram／Discord Bot

啟動全部已設定的通知 Bot：

```bash
ticket-bot --config config.yaml bot
```

只啟動其中一種：

```bash
ticket-bot --config config.yaml bot --platform telegram
ticket-bot --config config.yaml bot --platform discord
```

### 監測 Ticketmaster

```bash
ticket-bot --config config.yaml monitor "活動關鍵字" --interval 60
```

`monitor` 可以同時接受多個關鍵字：

```bash
ticket-bot --config config.yaml monitor "關鍵字一" "關鍵字二"
```

## 驗證碼收集與模型訓練

專案提供兩種資料流程：

1. 執行票務流程時收集驗證碼，再人工標註。
2. 使用教學模擬站自動收集答案並完成模型訓練。

### 流程 A：執行時收集與人工標註

#### 1. 啟用驗證碼收集

在 `config.yaml` 設定：

```yaml
captcha:
  engine: ddddocr
  collect_dir: "./captcha_samples"
```

之後正常執行 `run` 或 `watch`。程式遇到驗證碼時，會把樣本存入 `captcha_samples/`。

```bash
ticket-bot --config config.yaml run --event "Test"
```

#### 2. 人工標註圖片

```bash
ticket-bot --config config.yaml label --dir ./captcha_samples
```

操作方式：

- 輸入圖片中的正確文字後按 Enter。
- 直接按 Enter 跳過目前圖片。
- 輸入 `q` 結束標註。

標註結果預設存放在：

```text
captcha_samples/labeled/labels.json
```

#### 3. 整理成訓練資料

```bash
ticket-bot --config config.yaml prepare \
  --dir ./captcha_samples \
  --output ./captcha_training_data
```

輸出內容包括：

```text
captcha_training_data/
├── train/
├── labels.csv
└── charset.txt
```

`label` 與 `prepare` 負責標註及整理資料，不會自行訓練 ONNX 模型。

### 流程 B：教學模擬站完整訓練

訓練腳本位於：

```text
scripts/train/train_captcha.py
```

訓練額外需要 PyTorch、ONNX Runtime 與 Pillow：

```bash
python -m pip install torch onnx onnxruntime Pillow
```

如果尚未安裝 Playwright 瀏覽器：

```bash
playwright install chromium
```

#### 1. 收集模擬站驗證碼

```bash
python scripts/train/train_captcha.py collect --count 300
```

資料會存入：

```text
captcha_training_data/samples/
captcha_training_data/labels.json
```

#### 2. 訓練模型

```bash
python scripts/train/train_captcha.py train \
  --epochs 50 \
  --batch-size 32 \
  --lr 0.001
```

#### 3. 測試模型

```bash
python scripts/train/train_captcha.py test --rounds 30
```

測試會比較自訓練模型與預設 ddddocr 的辨識結果。

#### 4. 一次完成收集、訓練與測試

```bash
python scripts/train/train_captcha.py all \
  --count 300 \
  --epochs 50
```

### 訓練產物

完成後主要檔案位於：

```text
model/
├── captcha_model.onnx
├── charset.json
├── meta.json
└── best_model.pth
```

把 ONNX 模型與字元集填回 `config.yaml`：

```yaml
captcha:
  engine: ddddocr
  custom_model_path: "model/captcha_model.onnx"
  custom_charset_path: "model/charset.json"
```

重新執行時，若檔案存在，程式會優先載入自訓練模型。若要停用自訓練模型，將這兩個欄位設為空字串。

> `captcha_training_data/`、`captcha_samples/`、模型權重及標註資料可能很大，也可能包含執行時樣本。上傳 Repository 前請先確認 `.gitignore`。

## 設定檔說明

### 完整範例

```yaml
deployment:
  profile: local_desktop

events:
  - name: "Test"
    platform: ticketplus
    url: "https://你的授權測試網站/activity/活動識別碼"
    ticket_count: 2
    date_keyword: "2026-11-22"
    area_keyword: ""
    area_keywords:
      - "C"
      - "B"
    max_price: null
    sale_time: "2026-11-01T12:00:00+08:00"
    presale_code: ""

browser:
  engine: playwright
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
  custom_model_path: "model/captcha_model.onnx"
  custom_charset_path: "model/charset.json"
  collect_dir: ""

notifications:
  telegram:
    enabled: false
    chat_id: ""
  discord:
    enabled: false

proxy:
  enabled: false
  rotate: true
  servers: []

trace:
  enabled: false
  log_path: "./logs/tixcraft_trace.jsonl"
```

### 活動設定

| 欄位 | 說明 |
| --- | --- |
| `name` | 活動名稱，也是 `--event` 比對的文字 |
| `platform` | `tixcraft`、`kktix` 或 `ticketplus` |
| `url` | 活動頁面網址 |
| `ticket_count` | 希望選擇的票數 |
| `date_keyword` | 場次日期關鍵字；留空時選擇第一個可用場次 |
| `area_keyword` | 單一票區關鍵字，保留給一般平台及舊設定使用 |
| `area_keywords` | Ticket Plus 等流程依順序嘗試的票區關鍵字 |
| `max_price` | 最高可接受票價；`null` 或留空表示不限制 |
| `sale_time` | `countdown` 使用的開賣時間，必須包含日期、時間與時區 |
| `presale_code` | 活動需要預售代碼時使用 |

`area_keywords` 的順序代表優先順序。例如：

```yaml
area_keywords:
  - "C"
  - "B"
```

表示先嘗試 C 區，找不到符合條件的票時再嘗試 B 區。

### 瀏覽器設定

| 欄位 | 說明 |
| --- | --- |
| `engine` | `nodriver` 或 `playwright` |
| `headless` | 是否隱藏瀏覽器；登入與除錯建議設為 `false` |
| `user_data_dir` | 儲存 cookies、session 與登入狀態的目錄 |
| `pre_warm` | 倒數模式是否預先開啟頁面 |
| `lang` | 瀏覽器語言，例如 `zh-TW` |
| `executable_path` | Chrome 執行檔完整路徑；留空時自動尋找 |
| `api_mode` | Tixcraft API 模式；純瀏覽器操作使用 `off` |
| `turbo_mode` | 是否啟用較快速的欄位操作方式 |

### 驗證碼設定

| 欄位 | 說明 |
| --- | --- |
| `engine` | 驗證碼辨識引擎 |
| `beta_model` | 是否使用 ddddocr beta 模型 |
| `char_ranges` | 字元範圍；`0` 表示不限制 |
| `confidence_threshold` | 接受辨識結果的最低信心值 |
| `max_attempts` | 最大辨識與提交次數 |
| `preprocess` | 是否先進行影像前處理 |
| `custom_model_path` | 自訓練 ONNX 模型路徑 |
| `custom_charset_path` | 自訓練模型字元集路徑 |
| `collect_dir` | 驗證碼樣本收集目錄；空字串表示停用收集 |

## 多帳號 Session

每個 Session 必須使用不同的 `user_data_dir`：

```yaml
sessions:
  - name: "帳號A"
    user_data_dir: "./chrome_profile_a"
    proxy_server: ""
  - name: "帳號B"
    user_data_dir: "./chrome_profile_b"
    proxy_server: ""
```

並行執行：

```bash
ticket-bot --config config.yaml run --event "Test" --parallel
ticket-bot --config config.yaml watch --event "Test" --parallel
ticket-bot --config config.yaml countdown --event "Test" --parallel
```

同一個 `user_data_dir` 不可同時交給多個瀏覽器程序使用。

## 專案結構

```text
ticket_bot/
├── pyproject.toml
├── config.yaml.example
├── config.local.example.yaml
├── config.cloud.example.yaml
├── config.aws-tokyo.example.yaml
├── scripts/
│   ├── debug/
│   ├── diagnostics/
│   ├── release/
│   └── train/
├── src/ticket_bot/
│   ├── cli.py
│   ├── config.py
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
| `src/ticket_bot/cli.py` | 命令列入口與各指令流程 |
| `src/ticket_bot/config.py` | 載入 YAML、環境變數與部署設定 |
| `src/ticket_bot/browser/` | NoDriver／Playwright 瀏覽器抽象層 |
| `src/ticket_bot/platforms/` | 各票務平台流程與頁面解析器 |
| `src/ticket_bot/captcha/` | 驗證碼辨識、收集與資料整理 |
| `src/ticket_bot/notifications/` | Telegram／Discord 通知 |
| `src/ticket_bot/proxy/` | Proxy 選擇與輪替 |
| `src/ticket_bot/rl/` | 自適應重試與策略實驗模組 |
| `src/ticket_bot/utils/` | 重試、時間同步等共用工具 |
| `scripts/train/` | 驗證碼收集、產生、標註及模型訓練腳本 |
| `tests/` | 單元測試與流程測試 |

## 常見問題

### 找不到 `config.yaml`

```bash
cp config.local.example.yaml config.yaml
```

也可以直接指定完整路徑：

```bash
ticket-bot --config /完整路徑/config.yaml run --event "Test"
```

### 找不到 `ticket-bot`

```bash
source .venv/bin/activate
python -m pip install -e .
```

確認實際載入位置：

```bash
which ticket-bot
python -c 'import ticket_bot.cli; print(ticket_bot.cli.__file__)'
```

### Chrome 無法啟動

先確認 Chrome 可以正常開啟。若程式無法自動尋找，請設定執行檔完整路徑：

```yaml
browser:
  executable_path: "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
```

macOS 必須指定 `.app/Contents/MacOS/` 內的執行檔，不能只指定 `.app` 目錄。

### 已經登入，但執行時仍顯示未登入

確認 `login` 與 `run` 使用同一份設定檔和相同的 `user_data_dir`：

```bash
ticket-bot --config config.yaml login --platform ticketplus
ticket-bot --config config.yaml run --event "Test"
```

不要同時用另一個 Chrome 程序開啟相同的 profile。

### `countdown` 找不到活動

先確認：

1. 指令有使用正確設定檔。
2. `events[].name` 可以被 `--event` 部分比對。
3. 活動有設定 `sale_time`。
4. `sale_time` 是含時區的 ISO 8601 格式。

正確範例：

```yaml
sale_time: "2026-11-01T12:00:00+08:00"
```

正確指令：

```bash
ticket-bot --config config.yaml countdown --event "Test"
```

### 活動、場次或票區沒有被選中

依序檢查：

1. `--event` 是否能比對 `events[].name`。
2. `date_keyword` 是否和頁面顯示日期一致。
3. `area_keyword`／`area_keywords` 是否和票區名稱一致。
4. `ticket_count` 是否超過可購買數量。
5. `max_price` 是否排除了所有票種。

開啟詳細日誌：

```bash
ticket-bot --verbose --config config.yaml run --event "Test"
```

### `watch` 一直重試

`watch` 只會持續檢查頁面狀態，不保證一定有符合條件的票券。請確認活動仍可購買、登入狀態有效，而且日期、票區、張數和最高價格沒有互相衝突。

### Playwright 找不到 Chromium

```bash
playwright install chromium
```

或在 `config.yaml` 指定本機 Chrome：

```yaml
browser:
  executable_path: "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
```

### 模型訓練找不到資料

檢查：

- `captcha_training_data/labels.json` 是否存在。
- `captcha_training_data/samples/` 是否有圖片。
- 人工標註流程是否已產生 `captcha_samples/labeled/labels.json`。
- 執行指令時是否位於專案根目錄。

## 使用提醒

- 本專案不保證取得票券。
- 使用前請確認已取得網站或測試環境的操作授權。
- 請自行確認活動規則、場次、票區、張數、訂單金額與付款內容。
- 不要將 `.env`、瀏覽器 profile、cookies、token、驗證碼樣本或其他敏感資料提交到 Repository。
