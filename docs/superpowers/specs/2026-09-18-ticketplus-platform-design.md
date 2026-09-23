# Ticket Plus 平台整合設計

日期：2026-09-18

## 目標

在既有 `ticket-bot-public` Python 專案中新增 Ticket Plus 遠大售票平台，沿用目前的 `nodriver`／Playwright 瀏覽器抽象、Chrome profile、事件設定、單帳號與多帳號執行模式。

第一版的成功終點是：依使用者設定完成場次、票種、張數與可用座位選擇，進入訂單確認或付款前頁面後停止，將付款操作交回使用者。程式不得點擊會送出信用卡、ATM 或其他付款的按鈕。

主要公開驗收樣本：

```text
https://ticketplus.com.tw/activity/6d2868aeec40f3676950edc8fb70a1f4
```

此活動包含 `S區 NT$4,000` 與 `一般區 NT$2,500`，皆為站席，可用於驗證活動頁、場次、票種、張數與訂單確認流程。劃位流程使用測試 fixture 驗證同排相鄰座位演算法，待取得真實劃位活動 DOM 後再校正頁面 selector。

## 範圍

### 包含

- `platform: ticketplus` 的設定、CLI 分派與登入入口。
- 公開活動頁與活動 ID 解析。
- 使用既有 Chrome profile 的登入狀態。
- `run` 與 `watch` 指令。
- 場次日期篩選。
- 單一票區關鍵字與有順序的候選票區。
- 最高票價限制。
- 站席票種與張數選擇。
- 劃位票同區、同排、連續座號選擇。
- 流量管制、尚未開賣、售完、登入失效與人工驗證狀態。
- 進入訂單確認或付款前頁面後停止。
- 結構化診斷紀錄與單元／流程測試。

### 不包含

- 自動登入或在設定檔保存 Ticket Plus 帳密。
- 自動輸入 OTP。
- 自動解答或繞過 CAPTCHA／人機驗證。
- 自動送出信用卡、3D 驗證、ATM 或任何付款。
- Ticket Plus 私有 API 高速模式。
- 在官方網站建立測試訂單。
- 對無法從 DOM 或 SVG 取得座位資訊、僅以 Canvas 呈現的座位圖自動選位。

## 設定相容性

沿用現有 `config.yaml` 的 `events` 結構。既有 Tixcraft 與 KKTIX 設定不需修改。

```yaml
events:
  - name: "Ticket Plus 測試"
    platform: ticketplus
    url: "https://ticketplus.com.tw/activity/6d2868aeec40f3676950edc8fb70a1f4"
    ticket_count: 2
    date_keyword: "2026-11-22"
    area_keyword: "S區"
    area_keywords:
      - "S區"
      - "一般區"
    max_price: 4000
    sale_time: ""
    presale_code: ""
```

新增欄位皆為選填：

- `area_keywords: list[str]`：依序嘗試多個票區或票種。非空時優先於 `area_keyword`。
- `max_price: int | null`：單張票價硬上限。未設定表示不限制。

相容規則：

1. `area_keywords` 非空時，依清單順序比對。
2. 否則，將非空的 `area_keyword` 當成唯一候選。
3. 兩者皆空時，依頁面順序選擇第一個符合張數且不超過 `max_price` 的可售票種。
4. `max_price` 永遠是硬限制，候選降級不得突破。

## 架構

### `ticketplus_parser.py`

只負責純資料解析與選擇，不操作瀏覽器：

- 解析及驗證 Ticket Plus 活動網址與活動 ID。
- 將活動頁、場次頁、票種頁、座位圖與訂單摘要轉成穩定的 metadata。
- 正規化中文、英文、全半形空白、價格與日期。
- 建立場次選擇計畫。
- 建立有順序的票種選擇計畫。
- 對 DOM／SVG 座位資料尋找同區、同排、連續座號。
- 驗證訂單摘要是否符合票種、張數、票款小計及金額上限；服務費、取票費等附加費用須獨立呈現，不得混入單張票價判斷。

解析器不得依賴登入 cookie、瀏覽器物件或即時網路，確保 fixture 可完整測試。

### `ticketplus.py`

實作 `TicketPlusBot`，沿用 `BrowserEngine` 與 `PageWrapper`：

- 啟動與關閉瀏覽器。
- 使用 session 或全域 `user_data_dir`。
- 開啟活動頁與登入頁。
- 辨識頁面狀態並執行狀態轉移。
- 呼叫 parser 產生選擇計畫。
- 以 DOM selector 與受控 JavaScript 設定場次、票種、張數及座位。
- 等待流量管制與人工驗證完成。
- 進入訂單確認／付款前頁後停止。
- 實作 `run()` 與 `watch()`。

第一版不走私有 API。DOM selector 會集中定義，不散落在流程各處；若頁面改版，錯誤必須包含目前 URL、辨識到的狀態、場次與票種摘要。

### `cli.py`

- `_create_platform_bot()` 支援 `ticketplus`。
- `login --platform` 增加 `ticketplus`，登入頁使用 `https://ticketplus.com.tw/`。
- `run` 與 `watch` 的平台白名單加入 `ticketplus`。
- Ticket Plus 不使用 Tixcraft `api_mode`。
- 成功時沿用現有保持瀏覽器開啟的行為。

### `config.py`

在 `EventConfig` 增加：

```python
area_keywords: list[str] = field(default_factory=list)
max_price: int | None = None
```

設定載入需接受舊 YAML，不得要求既有事件新增欄位。

## 狀態機

```text
ACTIVITY
  -> LOGIN_REQUIRED
  -> SALE_NOT_STARTED
  -> SESSION_SELECT
  -> QUEUE
  -> TICKET_SELECT
  -> GENERAL_ADMISSION | SEAT_MAP
  -> ORDER_REVIEW
  -> PAYMENT_BOUNDARY
```

### 狀態行為

- `ACTIVITY`：等待 Vue 頁面完成渲染，解析標題與場次。
- `LOGIN_REQUIRED`：顯示清楚提示；`run` 不輸入帳密。使用者可先執行 `login` 保存 session。
- `SALE_NOT_STARTED`：`run` 回報後結束；`watch` 依 interval 重試。
- `SESSION_SELECT`：依 `date_keyword` 選擇可售場次；留空時選第一個可售場次。
- `QUEUE`：由網站原頁等待放行，不建立額外分頁或主動繞過限制。
- `TICKET_SELECT`：依候選順序、價格上限與張數選擇票種。
- `GENERAL_ADMISSION`：不執行座位選擇。
- `SEAT_MAP`：每個候選票區尋找同排連號；找不到時嘗試下一候選，不接受分開座位。
- `ORDER_REVIEW`：解析票種、張數、票款小計、附加費用與總額，與選擇計畫交叉驗證。
- `PAYMENT_BOUNDARY`：辨識付款或銀行授權控制項並停止，不點擊。

若人工驗證出現，程式保留同一頁並等待；驗證完成後重新辨識狀態並繼續。若超時，回報人工驗證未完成，不嘗試規避。

## 座位演算法

輸入座位至少包含：

```text
area, row, seat_number, available, selectable, element_key
```

處理順序：

1. 依 `area_keywords` 排序候選區域。
2. 排除不可售、不可選與超過 `max_price` 的項目。
3. 在同一區域內依頁面／座位圖順序走訪排次。
4. 將可解析為整數的座號排序，搜尋長度等於 `ticket_count` 的連續窗口。
5. 找到後回傳每個座位的 `element_key`。
6. 找不到時嘗試下一候選區域。
7. 全部失敗時回報沒有足夠相鄰座位；`watch` 繼續監測，`run` 結束。

座號無法解析或座位圖只以 Canvas 呈現時，第一版不得猜測座標或隨機點擊，而是回報不支援的座位圖結構並保持資料可診斷。

## 安全邊界

- 登入使用既有 Chrome profile，設定檔不新增帳號、密碼、OTP 或付款資料欄位。
- 不自動解答或繞過 CAPTCHA。
- 不點擊信用卡、ATM、3D 驗證或付款送出控制項。
- 只有在訂單摘要符合選擇計畫時，才將流程標記為成功；票款小計必須等於單價乘以張數，總額必須等於票款小計加上頁面列出的附加費用。
- 自動診斷紀錄不得寫入完整 cookie、Authorization header、會員個資或付款資料。
- 測試只使用本地 fixture 與 fake browser，不在官方網站建立訂單。

## 錯誤處理

所有失敗訊息至少包含頁面狀態與可採取的下一步：

- 登入失效：提示執行 `ticket-bot login --platform ticketplus`。
- 尚未開賣：記錄場次與售票狀態。
- 沒有符合票種：列出已解析票種、票價與狀態。
- 張數不足：列出候選票種與最大可選數量。
- 沒有相鄰座位：列出已嘗試區域。
- 流量管制超時：保留目前 URL 並允許 `watch` 重試。
- 頁面未知：停止點擊，記錄 URL、標題及已辨識控制項摘要。
- 訂單摘要不符：停止，不進入付款邊界。

## 測試

### Parser 測試

- 活動 URL／ID。
- 活動頁、登入視窗、尚未開賣、流量管制。
- 日期關鍵字選場次。
- `area_keyword` 舊設定。
- `area_keywords` 候選順序。
- `max_price` 硬上限。
- 站席票種與張數。
- 同排相鄰座位。
- 無相鄰座位時切換候選。
- 訂單摘要一致與不一致。

### Flow 測試

使用 fake `PageWrapper` 驗證：

- 已登入站席流程到付款前停止。
- 登入失效時不輸入帳密。
- 排隊後重新辨識狀態。
- 人工驗證後續跑。
- 售完時 `watch` 重試。
- 未知頁面不點擊。
- 付款控制項永遠不被點擊。

### 回歸測試

- `test_config.py` 驗證新舊 YAML。
- CLI 測試驗證 `login/run/watch` 支援 `ticketplus`。
- 執行全部既有 pytest，確保 Tixcraft 與 KKTIX 不受影響。

## 修改檔案

```text
src/ticket_bot/config.py
src/ticket_bot/cli.py
src/ticket_bot/platforms/ticketplus.py
src/ticket_bot/platforms/ticketplus_parser.py
tests/test_config.py
tests/test_ticketplus_parser.py
tests/test_ticketplus_flow.py
tests/fixtures/ticketplus/*
config.yaml.example
README.md
```

不修改 `BrowserEngine`／`PageWrapper` 介面，除非測試證明現有抽象無法安全完成必要操作；若發生此情況，必須先停止並重新確認設計。

## 驗收條件

1. 舊的 Tixcraft／KKTIX YAML 可原樣載入。
2. `ticket-bot login --platform ticketplus` 可開啟 Ticket Plus 並保存 Chrome profile。
3. `ticket-bot run` 能分派 `platform: ticketplus`。
4. 指定樣本可解析活動名稱、`2026-11-22` 場次、S區與一般區設定。
5. 站席 fixture 可完成票種與張數選擇並停在付款前。
6. 劃位 fixture 能選出指定張數的同排連號座位。
7. 候選區域依 YAML 順序降級且永不突破 `max_price`。
8. 未登入、排隊、售完、人工驗證與未知頁面都有明確結果。
9. 所有新測試及既有測試通過。
10. 交付內容排除虛擬環境、Chrome profile、訓練圖片、模型與敏感設定。
