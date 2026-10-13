# Obsidian 安裝、操作與無記憶接續

首次安裝、搬機或新對話要操作 Obsidian 時，**先完整讀本檔，再執行相關步驟**。不預設代理懂 Obsidian，不依賴作者電腦、其他專案工具、舊對話或已開啟的 vault。這份文件隨 skill 安裝，初始化後在工作區 `docs/`；它是本產品的 Obsidian 操作指示來源。

## 官方依據

以下內容於 2026-10-02 直接核對 Obsidian 官網。開始安裝時重新開啟與本機步驟相關的官方頁；命令另以本機 `help` 核對。官網不可讀時，可用本檔及本機 help 的共同支援範圍，明示未重新核對網站；版本或操作有衝突便先查原文，不試猜參數。

| 官方頁 | 用途 |
|---|---|
| [Download and install](https://obsidian.md/help/install)、[官方下載](https://obsidian.md/download) | 安裝桌面程式 |
| [Manage vaults](https://obsidian.md/help/manage-vaults) | 建立新 vault、接上既有資料夾 |
| [Obsidian CLI](https://obsidian.md/help/cli) | 啟用 CLI、核對目標及操作筆記 |
| [Obsidian URI](https://obsidian.md/help/uri) | 開啟管理介面／既有 vault／筆記，取得 vault ID |

## 1. 確定本次實際位置

分開記錄套件位置、產品 workspace 和 **vault 的絕對路徑**。使用者已給路徑就沿用；尚未指定才詢問。名稱由該資料夾取得，不能套用範例名稱，不能改名來迎合程式。已有設定、筆記和其他專案檔案均保留。

操作前向使用者列出「Obsidian 程式位置」及「本次 vault 完整路徑」。Obsidian 是共用程式，已開啟的其他 vault 不是本次目標；只操作本次已核對名稱及完整路徑的 vault。不能因程式已啟動便操作當前筆記，也不能把套件測試當成關閉其他工作視窗或更改它們設定的授權。

已有 `brain.json` 時讀取其 vault 設定並解析實際路徑，再與使用者指定位置核對；不假定 workspace 本身就是 vault。產品初始化以 `--workspace` 及 `--vault` 選擇資料位置，`--vault` 可為 workspace 內任何不衝突的相對資料夾路徑。遇衝突或配置不同便回報「產品接入未完成」；不能另建平行庫、改名或手改設定冒充接上。

以下 `VAULT_ID`、`VAULT_PATH`、`NOTE_PATH` 都是本次讀回的值。`NOTE_PATH` 是庫內確實存在的相對檔案路徑；不能照抄範例、不省略目標來借用活動 vault 或活動筆記。

## 2. 安裝 Obsidian 桌面程式

先檢查本機是否已有 Obsidian。缺少時按官方下載頁選本機平台：Windows 下載 Universal 安裝檔並完成安裝；macOS 下載 Universal，開啟後拖入 Applications；Linux 按官方安裝頁選實際使用的套件方式。不要猜安裝器網址、靜默安裝參數或固定安裝位置。

已有程式先沿用，不重裝或另裝一套。應用程式版本與安裝器版本分開核對；若 CLI 的確要求更新安裝器，先說明官方最低要求、已讀回版本、影響及目前可完成的部分，另取得更新與必要重開視窗的批准。不把 CLI 不可用直接推定為必須重裝。

在本次安裝授權及平台權限內完成安裝並啟動程式。需要系統批准或代理不能操作桌面時，交付當前那一步的明確操作，保留「待完成」，不把下載檔案當作已安裝。Windows／macOS 首次啟動程式亦用來註冊官方 URI。

## 3. 建立或接上 vault

先開啟 Obsidian，從左下角 Vault profile 進入 Manage Vaults。首次開啟若已在管理畫面，直接使用當前畫面。

- **資料夾已存在**（包括使用者預建的空資料夾或產品已生成的資料夾）：選 Open folder as vault → Open → 選取 `VAULT_PATH` 本身 → Open。不能再建立同名子資料夾。
- **尚未建立資料夾**：選 Create new vault → Create，Vault name 填本次選定名稱，Browse 選該目標的父資料夾，再 Create；讀回新資料夾的實際位置。

核對開啟的是所選資料夾，有舊筆記便讀回其中一份。不要直接編改 Obsidian 的全域 vault 註冊檔，也不要臆造 `vault:create` 命令。URI 的 `new` 是新增筆記，不是建庫。

## 4. 啟用及確認官方 CLI

官網目前要求安裝器 1.12.7 或以上。CLI 會連接 Obsidian；程式未啟動時，首個命令會啟動它。啟用是共用程式設定，不是每個 vault 都要重做。

**代理可協助完成：** 使用者要求完整安裝且允許桌面操作，代理又具備該能力時，依下面的實際介面操作、註冊及讀回，不把工作丟給新手。先核對目標 vault 視窗；不改其他設定，不直接編改 Obsidian 全域設定檔或呼叫未公開的內部介面。沒有桌面能力、遇到只能由使用者批准的系統視窗，或使用者停止桌面控制時，才交付下列當前需要的人工步驟，完成後由代理接續驗證。不能用另一種自動化繞過使用者的停止。

**首次設定步驟：**

代理先按當前官方文件及實際介面確認「命令列介面」的位置，再協助使用者完成。不同版本與語言可能將它放在「一般」或「關於」等分組；以目前畫面為準。

1. 在所選 vault 視窗開啟「設定」。
2. 找到「命令列介面」（Command line interface）並開啟；已開啟便保持。
3. 依畫面提示完成命令列註冊。若出現錯誤，保留原文，按官方疑難排解處理。
4. 保持 Obsidian 開啟，由代理在新終端核對下方 help、目標 vault 路徑及已有筆記；空庫則先核對路徑與檔案列表。

Windows 使用與實際 Obsidian.exe 同目錄的 **Obsidian.com** 終端入口；不要以 GUI exe 的無輸出判成命令成功。macOS／Linux 使用官方註冊後的 `obsidian`。核對實際可執行檔後，依次執行：

```text
obsidian version
obsidian help
obsidian help vault
obsidian vaults verbose
```

從最後一項核對已登記名稱及路徑；ID 從目標 vault 在切換器的選單「Copy vault ID」取得，不能假定 `vaults verbose` 必然輸出 ID。名稱唯一時官方亦支援以名稱定位；同名時必須取得 ID。

## 5. 按官方語法操作指定 vault

安裝時可用官方 help 核對本機 CLI；但工作區的 AI 不可直接拼接原生 CLI。它只可經第 7 節的受控入口操作，並由入口固定把 **`vault=...` 放在子命令之前**。每個新對話及更換目標後，入口都先執行路徑核對，輸出必須與 `brain.json` 的 vault 指向同一資料夾，否則停止後續操作。

```text
tools/brain.py obsidian --cli "OBSIDIAN_CLI_PATH" --vault-id "VAULT_ID" check
tools/brain.py obsidian --cli "OBSIDIAN_CLI_PATH" --vault-id "VAULT_ID" read --path "NOTE_PATH"
tools/brain.py obsidian --cli "OBSIDIAN_CLI_PATH" --vault-id "VAULT_ID" open --path "NOTE_PATH"
tools/brain.py obsidian --cli "OBSIDIAN_CLI_PATH" --vault-id "VAULT_ID" search --query "QUERY"
```

`path` 是 vault 相對路徑；本入口拒絕 `file` 名稱解析、絕對路徑、`..`、links 與 junction，避免重名或越界。它沒有 Backlinks、Bases、寫入或任意 `command id=` 功能。Graph 保留原生 UI 手動入口：本機可用時才由桌面操作開啟並讀回，否則標為未驗證，不猜插件 ID 或畫面名稱。

一般 Obsidian 筆記有官方 `create`／`append` 操作，但本產品的正式知識入庫必須經工作單、唯一 writer 和完成回條；不能用原生新增筆記取代入庫流程。這份安裝指示不要求建立假筆記來充當驗收。

## 6. URI 開啟方式

CLI 尚未啟用時，可按官方 URI 開啟管理介面或既有 vault。所有參數值先百分比編碼，包括空格、中文及路徑分隔符；使用標準 URL 編碼工具，不手工拼未編碼的路徑。

```text
obsidian://choose-vault
obsidian://open?vault=ENCODED_VAULT_ID
obsidian://open?path=ENCODED_ABSOLUTE_NOTE_PATH
```

`choose-vault` 只開管理介面，之後仍按第 3 節操作。`open?path=...` 會尋找包含該筆記的已知 vault，不會替任意資料夾完成註冊。URI 成功交給作業系統不等於畫面已開到正確庫；仍讀回實際 vault 與筆記。

## 7. 產品入口與完成判斷

初始化後使用 brain.json 記錄且本機核實可用的 Python 執行 `tools/brain.py context`，依回傳規格接手原 job；不要求舊對話。`context` 及 `doctor` 是本地檢查，不是 Obsidian 畫面驗收。

`brain.py` 提供一條受控的唯讀入口，讓已安裝工作區不必直接拼接 Obsidian CLI：`tools/brain.py obsidian --cli <已核對的 CLI 完整路徑> --vault-id <明確 vault ID> <check|read|search|open>`。每次都先以 `vault=<ID> vault info=path` 核對結果正是 `brain.json` 的 vault，且只接受 vault ID，不接受可能重名的 vault 名稱。`read`／`open` 只接受已存在、非連結或 junction 的 vault 相對檔案；`search` 回傳 JSON；`open` 只表示已送出 requested，並不代表 UI 已驗證。CLI 缺失、逾時、錯誤、路徑不符或越界一律受阻；必要狀態回應的空白也會受阻，只有已存在空筆記的 `read` 可保留原文成功，不會退回 active vault 或 active file。正式知識寫入仍只可使用唯一 writer。

本受控入口不提供 Graph 命令執行，Graph 維持原生 UI 手動入口及獨立畫面驗收；它不接受任意 `command id=`，也不把 URI 或送出請求當作已開啟。

依實際證據分別回報：程式安裝、vault 已在 App 接上、CLI 啟用及路徑讀回、產品接入、正式首筆入庫。沒有桌面控制能力就給使用者第 3／4 節當前所需步驟，保留該項未驗證；CLI 可讀筆記也不等於 Graph／Bases 畫面通過。不能以檔案存在、URI 已送出或合成測試，把新電腦安裝標成全部完成。
