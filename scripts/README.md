# Scripts Layout

- `train/`: 驗證碼資料收集、標註、增強、訓練
- `debug/`: 單點除錯腳本，偏人工排查
- `diagnostics/`: live probe、壓測、實戰診斷腳本
- `release/`: 匯出公開版快照的工具與 allowlist

補充：
- `deploy/` 內多為私人環境運維腳本，通常不建議直接放進第一版公開快照。

原則：
- 穩定可重複使用的腳本才留在這裡
- 一次性實驗先放 `scratch/`
- 本機產物不要再回到 repo root
