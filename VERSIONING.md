# Versioning

本專案採用語意化版本 `MAJOR.MINOR.PATCH`，目前起始正式版本為 `2.0.0`。

## 原則

- 日常開發以 git commit 為主，不需要每次小改都升版。
- 只有在「值得部署、值得回退、值得記錄」的節點才升版。
- 版本號的單一來源是 `pyproject.toml`。
- 正式版本一律搭配 git tag，格式為 `vX.Y.Z`，例如 `v2.0.0`。

## 何時升版

### PATCH

用在不改變既有使用方式的修正。

適用情境：

- 修 bug
- 提高穩定性
- 調整 selector、captcha、retry、logging
- 修部署細節，但舊流程仍可直接沿用

範例：

- `2.0.0 -> 2.0.1`

### MINOR

用在新增功能，且舊用法基本仍相容。

適用情境：

- 新增 CLI 子命令
- 新增 bot 指令
- 新增通知管道
- 新增平台支援
- 新增 config 欄位，且舊 config 仍可使用

範例：

- `2.0.1 -> 2.1.0`

### MAJOR

用在破壞相容性的變更。

適用情境：

- `config.yaml` 結構調整，舊設定不能直接用
- CLI 指令、參數或行為有不相容變更
- 部署流程重做，舊 VM 操作方式不能直接沿用
- 預設行為改變，可能導致既有自動化流程失效

範例：

- `2.4.3 -> 3.0.0`

## 建議在這些時機升版

- 要部署到 VM 之前
- 做完一批完整功能之後
- 修掉重要 bug，之後可能需要回退或比對
- 想保留一個明確的穩定節點

## 發版最小流程

1. 修改 `pyproject.toml` 內的 `version`
2. 提交版本變更
3. 建立 git tag
4. 補簡短 release note

建議指令：

```bash
git add pyproject.toml VERSIONING.md
git commit -m "chore: bump version to 2.0.0"
git tag v2.0.0
```

## Release Note 格式

每次正式版本至少記錄以下其中幾項：

- `Added`: 新功能
- `Fixed`: 修正問題
- `Changed`: 行為調整
- `Breaking`: 不相容變更

不用寫很長，3 到 5 行就夠。

## 注意

- 在 worktree 很髒、內容尚未整理完成時，不建議直接打正式 tag。
- 若某次只是實驗或暫存節點，可只靠 commit，不一定要升版。
