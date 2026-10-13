# 由 AI 完成首次安裝

這是本包唯一的安裝執行指引。使用者入口為 `index.html` 的安裝 prompt；下列命令由代理執行，不交給新手。產品仍是 skill，沒有獨立 App 或常駐服務；需要閱讀時，`brain-guide` 與 `brain-nebula` 才會啟動或重用短暫、受限的本機 loopback 服務。本包完整提供 `brain-think`、`brain-url`、`brain-text`、`brain-image`、`brain-grow`、`brain-create`、`brain-tidy`、`brain-guide`、`brain-nebula`、`brain-check`、`brain-resume`、`brain-fix`、`brain-research`、`brain-update`；舊有 `brain-ask` 只保留為同一思考保存流程的相容別名。

## 1. 安裝前一次收齊

先作唯讀檢查：目前 AI 宿主與作業系統、代理執行位置、完整套件是否可讀、檔案與命令權限、桌面控制能力、現有 Python／Obsidian、技能位置和同名衝突。HTML 複製的 prompt 會帶教學頁位置；file URL 先正確解碼及核對實際路徑，不能把它當 shell 命令。若只有網頁／本機 localhost URL，不推定可讀使用者磁碟；無法定位完整包時和其他前置資料一起問清楚。不可把雲端沙盒當作使用者電腦。收到 ZIP 先用宿主現有的檔案／壓縮檢視能力列出條目，拒絕絕對路徑、`..`、符號連結及 junction；這不要求已有 Python。沒有安全檢視能力即受阻，不可先寫入 workspace。獲同意補齊 Python 後，才以 ZIP 的內置驗證入口安全解壓到新的暫存位置，核對身份記錄、profiles、profile-build 與全部 package 檔案；成功才可安裝。若同層 delivery manifest／SHA-256 存在，必須再核對最終 ZIP 位元組；存在但不符即停止。只有 ZIP 時可核對內部完整性，但外部來源身份仍屬未獨立核實。不要只取 SKILL.md。

集中問缺少的資料，最多三組問題：

1. 使用者的顯示名稱，以及想給筆記庫的名稱。
2. 新建或沿用；筆記存放位置。新手可用普通說法回答；由 AI 查出實際路徑並提出具體建議，不要求使用者懂相對路徑。已有資料必須指出原位置。
3. 列出實際缺少的工具、獨立 Python 環境與技能的具體安裝位置，確認本次安裝範圍。預先交代可預見的登入、作業系統批准或桌面設定步驟；不要索取密碼或金鑰。

已知資料不重問。以上未齊前不下載、不建立環境、不安裝、不改設定。平台安全批准仍按當時要求處理。未知新阻礙可以中途問必要一題，但不可將本可預知的資料拖到執行途中逐件問。

## 2. 判斷接入資格

套件根須有 `SKILL.md`、`profile-build.json`、`scripts/verify_package.py`、`scripts/verify_delivery.py`、`scripts/install_skill.py`、`scripts/setup_workspace.py`、`runtime/workspace/`。profile 使用本包 manifest 的實際值，公眾包應為 `public-portable`。

資料 workspace 必須在技能套件之外。vault 是 workspace 內的相對資料夾，名稱由使用者決定；AI 負責換算和核對。不能依顯示名稱推定職業、喜好或作者。

- 新建：只支援全新／空白 workspace；先 dry-run，再初始化。
- 已有本版 `brain.json`：核對套件 schema、vault 及 runtime 完整性後接續，不再次初始化。
- 受管的舊版 workspace（包括 v1.9.15）：只可由新正式包的 `<package>/runtime/workspace/tools/brain_update.py --workspace <舊 workspace> --check` 先讀回計畫；使用者要求更新時，狀態為 `checked` 才以同一條命令、同一個 workspace 執行 `--apply`。這不是 setup，不建立平行 vault；衝突或未完成交易不自動處理。來源只可為正式 GitHub `Adamchanadam/creative-second-brain` 發布，不能用 npm；舊全域 skill 保持不變。
- 未受管的舊 v1.4.5 或其他已有筆記：本包未提供遷移器；先核對並說明不能原地安裝。不得自行改名、搬移、覆寫或另建平行庫來冒充接入。使用者選擇另外新建才走新建分支。
- 已有不同版的同名全域技能：保留；只阻擋新安裝，提交實際差異與必要升級方案，不偷偷用專案副本繞過衝突。它不阻擋上述已受管 workspace 的原地更新，因更新不改全域 skill。

## 3. 工具及依賴由 AI 安裝

### 3.1 必需項目與零安裝電腦

不可假定新電腦有任何下列項目；由 AI 查出缺項、在第 1 節集中取得同意，然後自行補裝。

| 項目 | 如何查與補齊 | 完成證據 |
|---|---|---|
| Agent 宿主 | 確認實際電腦的檔案、命令、技能和桌面能力 | 原生技能清單及實際調用；不是只讀到檔案 |
| Python | 沿用真正的 3.11+ interpreter；全新 Windows 依下方官方流程補裝，優先 3.13 系列 | 真實 sys.executable 與版本 |
| pip、獨立環境 | 用該 Python 建立 venv；缺 pip 由 ensurepip 補上 | 環境內 -m pip --version |
| 全部 Python 依賴 | 版本以 runtime/workspace/requirements.txt 為唯一清單；目前涵蓋 Pillow、beautifulsoup4、`yt-dlp[default]` 與 tzdata，pip 同時安裝其依賴 | pip check；check_environment.py 實際 import、讀回 `python -m yt_dlp --version`，並核對 `Europe/London` 時區資料可用 |
| Obsidian 與 CLI | 缺程式才按第 5 節的專用操作文件安裝、開庫及啟用 | App 內正確 vault、CLI 路徑讀回 |
| Node、鎖定 Playwright 與 Chromium | 完整本機能力才需要；按當刻官方 Playwright 支援及 lockfile engine 交集核對，再安裝 workspace-local 依賴與 Chromium | `case_reproduction_capture.js --check` 實際 Chromium launch/close |
| Codex ImageGen schema | 從當前 Codex 內建工具 schema 確認；不能用 pip、API key、其他服務、靜態圖、搜尋圖或讀圖冒充 | schema 能力及真實受限邊界；不為安裝生成示範 |

本包不要求 Git、全域 Python 套件或 Obsidian 社群插件。Node／Playwright／Chromium 只屬完整本機能力，不可由基本 Python 成功推論。需要登入／切換 AI 宿主或費用時，不自動開帳戶或升級方案；預先提出真正必要的使用者動作。

### 3.2 Python 官方步驟（由 AI 執行）

依據於 2026-10-05 核對：[Python Windows 安裝與管理器](https://docs.python.org/3/using/windows.html)、[venv](https://docs.python.org/3/library/venv.html)、[pip](https://pip.pypa.io/en/stable/user_guide/)。安裝時重驗所用平台及當地 help；不盲猜旗標。

Windows 唯讀前置先用 `Get-Command python,py,pymanager -ErrorAction SilentlyContinue` 定位。WindowsApps 別名不等於已裝 Python；已確認是 Python Install Manager 才用 `pymanager list --format=json` 列本機 runtime。未確認有 runtime 前，不用 `python --version` 或 `py -c ...` 試啟動，因管理器可能自動下載。找到實際 interpreter 後再執行：

```powershell
& $pythonExe -c 'import sys; print(sys.executable); print(sys.version); raise SystemExit(0 if sys.version_info >= (3,11) else 2)'
```

`$pythonExe` 是剛才讀回的完整路徑。不要卸載或改掉已有 Python。確認缺 Python 並取得第 1 節同意後，Windows 主路線：

1. 讀官方文件的 Advanced installation；本機有 WinGet 時，先用 `winget show 9NQ7512CXL7T -e` 核對身份與來源，再以官方列出的 `winget install 9NQ7512CXL7T -e --accept-package-agreements --disable-interactivity` 安裝。遇來源協議或平台批准，只處理已說明的必要批准，不改安全設定。
2. WinGet 不可用時，從同一官方文件取得當前 AppInstaller 連結，按其 `Add-AppxPackage -AppInstallerFile <已核對官方連結>` 安裝，或由具桌面能力的 AI 操作官網安裝畫面。不要猜 EXE 的 silent 旗標；這是 Python 官方工具，不是本產品的安裝程式。
3. 在新終端核對 `pymanager help install`、`pymanager list --online 3.13`，確認本機架構可用後執行 `pymanager install 3.13`。不使用 force、update 或修改系統預設。
4. 用 `pymanager list --format=json 3.13` 取得真實位置，再跑上面的 interpreter 檢查。下載或管理器安裝成功不等於 Python 已可用。

macOS／Linux 先讀 [Python 對應平台官方文件](https://docs.python.org/3/using/index.html)，使用該平台官方安裝器／已核對的套件工具；不覆寫 OS Python，不自行加装未批准的套件管理器。本次沒有這兩平台真機驗收，不宣稱已驗證完整從零安裝。

### 3.3 完整 skill 與套件依賴

下列 `<python>` 等值由 AI 替換成核實值，每個路徑獨立引用。先用已核實 Python 檢查完整包：

```text
<python> <ZIP> --extract-to <new-temporary-folder>
<python> -B <package>/scripts/verify_package.py
<python> -B <package>/scripts/install_skill.py --archive <ZIP>
<python> -B <package>/scripts/install_skill.py --archive <ZIP> --apply
```

安裝器預設複製完整套件到當前使用者 `.agents/skills/creative-second-brain`，同內容可安全重跑；衝突或中斷保留現場，不覆寫。這是產品與發行資料夾，日常工作只從每個 workspace 的 14 個 canonical `brain-*` 入口開始；`brain-ask` 只作相容別名。`--skills-root` 只供明示的測試／自訂位置，不證明真實全域辨識。預設位置按 [Codex 官方技能文件](https://learn.chatgpt.com/docs/build-skills) 核對；其他 Agent 宿主先驗其官方安裝與入口契約，不能把 Codex 路徑當通用標準。沒有已驗適配就報能力缺口，不宣称支援。

使用 installer 回傳的完整技能位置作 `<skill>`。建立與 workspace 分開、尚不存在的專用環境（例如同一父資料夾的獨立工具環境；具體位置已在前置資料確認），不改全域 Python 套件：

```text
<python> -m venv <environment>
<environment-python> -m pip --isolated install --index-url https://pypi.org/simple -r <skill>/runtime/workspace/requirements.txt
<environment-python> -m pip check
<environment-python> -B <skill>/scripts/check_environment.py
```

Windows 的環境 Python 在 `Scripts/python.exe`，其他平台在 `bin/python`；直接使用完整路徑，不需要 activate 或修改 shell execution policy。缺 pip 才在該環境用 `-m ensurepip --upgrade`。驗收必須真正匯入 Pillow、BeautifulSoup、`yt_dlp` 與 `tzdata`、依 requirements.txt 唯一列出的 Pillow、beautifulsoup4、`yt-dlp[default]` 與 tzdata 核對目前鎖定版本，讀回 `<environment-python> -m yt_dlp --version`，並確認 `Europe/London` 時區資料可用；不能只看 pip 回傳成功。tzdata 讓任何沒有 IANA 時區資料的系統（包括常見的 Windows Python 環境）仍能保留研究主題預設的 `Europe/London` 與夏令時間語義。升級前先唯讀比對既有專用 venv 與本包 requirements；相符才沿用，不符只報出差異，並在已確認的更新範圍內才更新專用 venv，保留筆記、技能和設定，並重做 import、時區及 CLI 讀回；只有超出原同意範圍才詢問。四項直接鎖定 Python 依賴完成後只可稱基本 skill 可用；workspace 建好後才以 `<environment-python> -B <skill>/scripts/check_environment.py --workspace <workspace>` 核對完整本機依賴。完整本機能力還要在 `<workspace>/tools` 依 `package-lock.json` 安裝 workspace-local Playwright、以 hermetic `PLAYWRIGHT_BROWSERS_PATH=0` 安裝 Chromium，並在沒有預設該環境變數的新子程序實跑 `node case_reproduction_capture.js --check` 的 Chromium launch/close。Node 在安裝當刻必須同時符合 [Playwright 官方 System requirements](https://playwright.dev/docs/intro) 與本包 lockfile engine；本包不硬編會過期的最低版本，也不使用 `npm init`。browser、npm、pip cache 只放本次環境或已確認暫存位置，不污染全域。

完整本機能力的 AI 步驟是：在 `<workspace>/tools` 以 lockfile 執行 `npm ci`，再以 Playwright hermetic `PLAYWRIGHT_BROWSERS_PATH=0` 執行 `node node_modules/playwright/cli.js install chromium`，最後在沒有預設該環境變數的新子程序實跑 `node case_reproduction_capture.js --check`。Windows 由 AI 以該程序的環境設定 `PLAYWRIGHT_BROWSERS_PATH=0`；POSIX 以同一子程序環境設定，不把 shell 寫法交給使用者。這會把 Chromium 固定在 workspace 的 `node_modules/playwright-core/.local-browsers`，之後 capture 自己亦預設 hermetic `0`；不是全域瀏覽器或一次性 shell 成功。

YouTube 只可用現有公開 VTT 字幕並以 `yt-dlp --skip-download` 取得；不下載影片、音訊、縮圖或描述，不把描述／摘要當字幕，也不加入轉錄、ffmpeg、登入、cookie、外掛、API key、Git、Docker 或 WSL 作替代。

失敗按原因處理：ensurepip 的 PermissionError 先讀錯誤，確認是臨時目錄／沙盒限制還是目標權限；只用已批准的任務暫存及平台批准通道，不 chmod、更改 ACL 或關閉安全工具。pip 網絡失敗保留未完成，不改不明鏡像或停用 TLS；版本／import 失敗先核對 interpreter 與鎖定依賴，不無聲升級。每次修正後重跑該項檢查，再往下走。

## 4. 建立及接續工作區

用同一個環境 Python 執行：

```text
<environment-python> -B <skill>/scripts/setup_workspace.py --profile public-portable --workspace <workspace> --vault <relative-vault> --display-name <name> --dry-run
<environment-python> -B <skill>/scripts/setup_workspace.py --profile public-portable --workspace <workspace> --vault <relative-vault> --display-name <name>
<environment-python> -B <workspace>/tools/brain.py doctor
<environment-python> -B <workspace>/tools/brain.py context
```

`.candidate-init.json` 表示未完成，保留並讀回現場；不能刪掉標記重跑。`brain.json` 記錄實際 runtime_python、相對 vault 和 display_name；新代理先依 workspace 的 AGENTS.md 接續。搬機或 Python 路徑失效時，核實新環境後使用其真實路徑，不依賴舊電腦。

setup 建立完整 `brain-*` 技能入口。讓宿主開啟該 workspace，實際驗證技能清單辨識及明確調用；檔案存在不足以证明辨識。Codex CLI／IDE 可用 `/skills` 或教學頁列出的 `$brain-*` 入口；桌面按當前技能選擇器顯示，沒有出現時依官方指示刷新／重開。其他宿主未必支援 `$` 語法：必須在完成前說明，直接讀相應的 `brain-*` 技能後按其流程處理；自然語言替代句也必須包含技能名稱、操作和必要輸入，不能只留下「問題：」等半句。在驗收時仍須分清「直接讀技能檔執行」和「宿主已辨識」。

## 5. 接上 Obsidian 及圖片能力

完整讀取 `<skill>/runtime/workspace/docs/Obsidian操作與無記憶接續.md`，按其中官網與本機 help 核對；已有 Obsidian 沿用，缺少才由 AI 安裝。本產品的使用者步驟僅保留必要 Obsidian 介面設定：AI 先交付實際 vault 絕對路徑，讓使用者依教學選 Open folder as vault，再啟用 CLI；不能要求他輸入終端命令。已有設定便不重做，使用者明示交給 AI 且有桌面能力才代操作。完成後如需讀取、搜尋或開啟這個 vault 的筆記，AI 只可使用 `tools/brain.py obsidian --cli <已核對 CLI> --vault-id <明確 ID> <check|read|search|open>`；它每次先讀回 `vault info=path` 對照 workspace 的 `brain.json`，不使用 active vault/file，且 `open` 只可報 requested、未驗證 UI。Graph 維持官方原生 UI 入口；因沒有可通用核實的 command ID，不能以 CLI 任意 command 或 URI 聲稱已開啟。空庫關聯圖可空白，不造知識。首份正式材料完成後，應核對關聯圖已顯示概念；若畫面沒有套用預設，取得畫面或實際設定資料夾再診斷，不能只因 `.obsidian/graph.json` 存在就稱成功。不能直接改 Obsidian 全域註冊檔或把送出 URI 當成已開庫。沒有桌面控制不會單獨阻止此人工設定分支；沒有可用檔案／命令能力則不能完成本機安裝。

圖片能力檢查只讀當前 Codex session 的 ImageGen schema，不為安裝而生示範圖片或知識。正式新資訊圖只可用該內建能力，不能改用 API key、其他服務、靜態圖、搜尋圖或讀圖替代。若 schema 缺失，明確標示「正式資訊圖生成未就緒」；安裝可保存已完成部分，但不能交付「全部可用」。只有讀圖或一般聊天能力的宿主不足以完成全部入庫。

## 6. 給新手的完成交付

AI 只可先給一個整體裁決：「完整可用」、「基本可用但明列受限功能」或「受阻未完成」。其後逐項提供 package／版本、專用 venv 四項依賴與 yt-dlp CLI／時區資料、正確 Obsidian vault／CLI、14 個 canonical skill、Node／鎖定 Playwright／Chromium、Codex ImageGen schema、首頁、nebula、閱讀 HTML 與 Graph 的實際證據；單項不各自宣稱完整可用。首頁只在已實際啟動 loopback 後回報 `index.html#prompts`；nebula 同樣回報實際 URL。沒有安全瀏覽器工具時只給 URL，不稱已開啟。技能、依賴或 Obsidian 缺項需明列，不能用另一項 ready 抵銷。

向使用者交付自己的首頁連結、資料位置、完整 `brain-*` 入口的短句，以及實際能力限制。新库保持空白；首筆正式入庫是之後獨立的任務，不以造示範資料充當安裝驗收。需要使用者實測前，先把 AI 可做的項目完成及讀回。任何仍未驗證的宿主／原生介面項目如實列出，不叫使用者代做工程除錯。

完整備份含 workspace 的 brain.json、vault、inputs、outputs、tools/docs 及完整 skill。更新 skill 與遷移既有 workspace 是不同操作；這個首次安裝流程不自動升級舊資料。沒有發布、排程或外傳筆記的默示授權。
