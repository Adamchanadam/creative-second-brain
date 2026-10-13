---
name: creative-second-brain
description: 在使用者指定的 Obsidian workspace 建立或使用完整可攜的創造型第二大腦；保存、提問、創作、整理、研究與無舊對話接續均不連接私人來源或發布。
---

# Creative Second Brain（可攜完整技能）

此技能是隔離的可攜交付來源，只在使用者明確指定的 workspace 操作；不猜 vault 名稱、作者姓名或舊機器路徑。

## 入口

安裝請完整讀 [首次安裝指引](references/first-use.md)，先作唯讀檢查並一次收齊前置資料，再由 AI 完成工具、依賴、完整技能及使用者 workspace 的安裝與驗收。新手入口是隨包 `index.html` 和 `install-prompt.txt`，不要求使用者操作終端。

日常使用先讀 [命令表](spec/command-registry.json) 與 [資料 ownership](spec/ownership.md)，再讀指定 workspace 的 AGENTS.md 和 brain.json。用設定中的實際 Python 執行 `tools/brain.py`；`brain-think`、`brain-url`、`brain-text`、`brain-image`、`brain-grow`、`brain-create`、`brain-tidy`、`brain-guide`、`brain-nebula`、`brain-check`、`brain-resume`、`brain-fix`、`brain-research`、`brain-update` 的宿主入口與工具子命令不是同一件事。`brain-ask` 只是相容別名，必須轉到同一個 `brain-think` 正式流程。`brain-update` 只路由至 workspace 的 `tools/brain_update.py`：先讀回 `--check`，再按使用者已要求的更新執行 `--apply`；不自動解決衝突，不碰私人資料或全域舊 skill。

完整功能以命令表為唯一基準；宿主是否把它們顯示為 slash command 需另行核對。多步、較長、除錯、根因修復或驗收工作，所有 `brain-*` 入口都會使用同一張可讀任務進度卡。文字入庫保留完整正文、提交者與原作者／被引用作品分離；庫內提問使用 `thinking_v2`，有地圖短標時需有 `visual_check`。研究只能按使用者直接要求啟動，PUBLIC 不提供背景排程。正式圖片、真實來源與語義審閱仍要按實際工具結果驗收。

## 邊界

- `runtime/` 是此產品來源內可編輯的通用核心；建置只從這個來源複製，不讀取任何其他工作區。
- `scripts/build_profiles.py` 僅從本可攜 source 生成隔離 package；生成物不可手改。
- 舊輸入只可用 `brain compat prepare`／`ingest` 的既有相容流程處理；`brain legacy-preview` 只讀預覽舊個人段落。兩者都不會原地升級或改寫既有 vault、歷史作者、來源或筆記。
- 不從此可攜包推論其他工作區已切換、已同步、已發布，或任何未實測的圖片能力。
