# 可攜完整技能資料 ownership

`brain.json` 是 runtime-owned workspace 設定：profile、`display_name`、`vault`、套件版本及 runtime hash。使用者可改展示名稱；這只改設定，不得回寫既有來源、作者、引用、application note 或 thinking record。

vault 內的來源正文、registry、來源筆記、application note、思考紀錄、使用者 Obsidian 設定及輸入材料均是 user-owned product data。runtime 可在使用者明確執行對應命令時，按該命令既有契約寫入；setup 不會為姓名或 profile 重寫它們。

新資料使用通用 `## 個人化消化`。來源正文中的人名屬原文內容，永不因個人化而刪改。

資訊圖 footer 沿用通用簽名 `(創造型 2nd Brain)`；不把任何私人簽名設為所有使用者的必要輸出，也不在本批新增動態 footer 客製。真實圖片生成與 footer 可見文字須按實際輸出驗收；在回條前一律是 `pending`，不可冒充已驗證。

`runtime/` 是此產品來源內可編輯的通用核心。`scripts/export_runtime.py` 只複製並記錄這個核心的 SHA-256；套件完成後不讀取任何其他工作區。

開發時以完整命令表、export provenance、轉換紀錄和 focused tests 作檢查；這不是正式安裝信任。新功能在聲稱可用前，還要從正式入口完成一次受控 dry-run，讀回實際工作單、目標產物或阻擋結果及預期副作用為零的證據。沒有使用者提供的真實外部來源時，只可稱為離線機制驗收，不能冒充真實來源全鏈。`build_profiles.py` 為隔離 profile 產生 source manifest；workspace 的 `brain.json.runtime_files` 與 `entry_files` 則在每次實際操作前由 `brain doctor` 檢查。

已受管 workspace 的日後更新若遇到本機已改 runtime 檔，預設必須停止。只有 `manage_workspace.py` 的 `--update-resolution` 可處理已逐檔審閱的例外：決議必須同時綁定 workspace、vault、`update` 模式、候選套件、目前 hash、候選目標 hash、理由和時間。它有三個互斥分類：原設定已有該路徑時，必須再綁定原登記 hash；原設定明確沒有該鍵而普通 runtime／入口檔已存在時，才可用「既有未管理檔接合」；只有既已受管、非私有且不屬 `brain.json`、`tools/brain.py` 或 12 個 `brain-*` 技能入口的通用 runtime，才可用「已審核主機適配」。主機適配保留目前檔案，並把原登記 base、候選 base 與主機 hash 一同寫入候選綁定的 overlay；候選或主機檔案改變後必須重新審閱。前兩類只可把現有衝突換成精確替換；三類都不可新增、修改 `brain.json`、移除檔案、越過私有 vault 或接管 inventory 外檔案。恢復時會讀回原設定與交易備份，證明原本擁有狀態及 before bytes；所有既有鎖、交易、backup、rollback、writer 與 research gate 繼續生效。

若 host workspace 有必須保留的私有 runtime，`scripts/sync_shared_surface.py` 只可同步 `tools/brain.py` 和 12 個 `brain-*` 的 `SKILL.md`。它會先阻擋未登記的 runtime 差異，再把逐檔 base／host SHA-256 寫入受限設定；不會替換其他 runtime、Vault、Obsidian 資料或 OPS 排程。 已登記的 host runtime 再次修改後，也必須先核對改動來源及相關功能測試，再以逐檔 `--host-runtime-override` 產生 plan，審閱精確差異後以同一 `--plan-hash` apply；不得手改 hash 或繞過入口。plan 後檔案再變須重新審閱。完成條件包含正式 `tools/brain.py doctor`、`context` 及受影響的日常入口操作；底層函式測試通過不能代替入口驗收。
