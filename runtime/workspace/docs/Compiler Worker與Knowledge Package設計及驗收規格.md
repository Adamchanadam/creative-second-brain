# Compiler Worker 與 Knowledge Package 設計及驗收規格

本文件是可攜「創造型 2nd Brain」的三入口工作、Knowledge Package、編譯、恢復和正式完成唯一 owner。它不保存某台電腦的已通過狀態。先以 `python -B tools/compiler_worker_v2.py capabilities` 讀回程式能力；能力齊備不等於外部 provider、圖片生成或真人 UAT 已通過。

## 1. 決策與邊界

入庫編譯期間，Knowledge Package（KP）是單一內容真源。source note、application、KP notes、relations、PNG 引用與 outcome 只能由同一份已驗證 KP 投影。完成後的 Vault Markdown、registry 與 assets 是正式資料與閱讀/檢索真源；HTML/索引可重建。不得從投影無痕反改 KP 或完成收據。

新來源唯一入口是 `ingest_rail.py`，底層使用 `compiler_worker_v2.py`；正式 Vault writer 仍只有 `ObsidianVaultAdapter`。相容 pipeline 僅用於既有工作修復與文字附現成 PNG 保留，後者完成證據由來源與 writer 規格定義，不冒充 v2。

一條規則只有一個 owner：來源/保存/歸屬/writer 見 [來源規格](URL到輸出模板規格.md)；圖像全文、母版、生成和反審見 [圖像規格](資訊圖生成規格.md)；idea/bridge/問答/整理見 [語義圖規格](Obsidian語義索引與關聯圖落地方案.md)；閱讀投影/Canvas 見 [閱讀層](成果頁索引資料層設計.md)；驗收分工見 [驗收](多層驗收機制.md)及[QC路由](分層QC機制.md)。

## 2. 唯一流程

接單 → 不可變 Input Bundle → canonical source → 一次合格 KP/計劃 → 每頁 Codex imagegen receipt → 唯一 writer → 本篇讀回 → 增量 outcome/不可變 completion receipt → 本篇 idea candidate receipt。

同一 agent 沿同一來源工作直到完成、明確阻塞或安全 checkpoint；不要讓使用者重貼多段指令。執行器可遵守同一資料契約，但正式新資訊圖只用 Codex 內建 imagegen/gpt-image-2。本套件沒有自動背景服務，不能把 queued、工具存在或派發要求當成已運行。

## 3. 三類真源與 Input Bundle

每次只接受一條來源，先保存不可變 Input Bundle，再開始 AI 工作。

新 v2 文字與 URL 入庫一律保存完整的可讀來源正文：`source_capture`、canonical body、Knowledge Package 的 `full_body_required`、source note 的「正文保存區」及 Vault 讀回必須連成同一條來源鏈。摘要、TL;DR、知識點與資訊圖只是衍生層，不能取代正文；`article`、`paper` 等 profile 只決定分析覆蓋和速度，不降低保存要求。使用者在對話貼文時，接單前須把貼文全部內容與輸入檔逐段核對；合理的標題與排版整理可保留，但刪段、截尾或改寫成摘要必須停在接單前。URL 擷取、快照與後備路徑只按 §7.2「URL 擷取契約」執行；若不能判定擷取完整，標記 `partial_capture` 並停止正式寫入。

| 真源 | 必存資料 | 唯一身分 | 不可接受的替代 |
|---|---|---|---|
| URL | 標準化 URL、可讀正文、capture proof、正文錨點 | `url:<normalized-url-hash>` | 摘要、導覽列或只讀到片段的網頁 |
| 文章／Markdown／文字 | 原始 UTF-8 bytes、SHA-256、canonical body、文字錨點；若正文引用外部作品，另存獨立 citation evidence，成果頁須分開顯示提交文字標題與被引用作品資料及 URL | `user_provided_text:<sha256>` | 把被引用作品作者／題名誤當成評論貼文作者／題名、把引用 URL 當成輸入網址身份、遺失正文或引用 URL 未投影 |
| 圖片 | 原圖 bytes、MIME、尺寸、SHA-256、逐字轉錄、`IMG-*` 視覺錨點 | `user_provided_image:<sha256>` | OCR 摘要取代原圖，或 imagegen 圖取代原圖 |

Input Bundle 只保存 root-relative 檔案位置，不保存使用者機器的原始絕對路徑。相同身分重交必須回到同一工作或同一完成成果，零重複寫入。

URL 的正規網址與來源身份必須由 Compiler Worker 與其帳本共用同一標準化實作；不得由各入口自行複製規則。該實作須移除 fragment、已知追蹤參數與預設連接埠，固定查詢參數次序，並在建立工作前拒絕網址憑證或敏感查詢欄位。

### 3.1 來源 profile 與覆蓋策略

Input Bundle 必須保存 `source_profile`，用來決定內容覆蓋、時間預期及 QC 快慢路徑。profile 只控制「怎樣讀完整」，不可把來源主張改寫成 AI 推論，也不可刪走會改變結論的方法、限制、數據或反例。

| Profile | 使用時機 | 覆蓋策略 | 預設 QC | 不可做 |
|---|---|---|---|---|
| `article` | 一般網文、訪談、產品介紹、觀點文章 | 讀正文主體與必要上下文；排除導覽、廣告、留言、推薦文章與網站殼層 | 單篇快路徑：來源身分、KP / PNG receipt、source-local readback、當前 outcome / Obsidian 入口、新增 idea candidate | 不因網站模板雜訊增加 KP 或資訊圖頁數 |
| `paper` | arXiv、公開論文、技術報告、研究預印本 | 以 abstract、introduction、method、results、discussion、conclusion、限制及核心圖表為主；references、appendix、大型公式推導、致謝及 HTML 導覽預設只作查證背景，除非它們直接支撐主結論 | 單篇快路徑加論文 section coverage receipt；只需證明核心研究問題、方法、結果、限制與可應用 idea 已覆蓋 | 不把 bibliography、appendix 或每個表格 row 逐一變成 KP；不把長度當成全庫回歸理由 |
| `full_body` | 舊工作相容 profile；新 rail 全文固定保存，難度只選 article / paper | 保存 canonical body 及 SHA-256；KP 仍只抽可重用主張、方法、限制、數據與應用橋 | source-local readback 必須加 canonical body hash / representative anchors；其他檢查仍按是否改工具或 graph 決定 | 不把全文保存等同逐段摘要；不因全文模式自動跑全庫健康檢 |

新工作無論 profile 都保存全文；公開論文選 `paper`，一般文章選 `article`。舊 `full_body` 工作沿用原保存契約讀回，不把它用作新工作免計時的選項。

## 4. Knowledge Package v2

KP 是唯一可被寫入器接受的結構化物件，不是 AI 任意寫出的 Markdown。它至少包含：

1. `source`: 身分、保存模式、原始／canonical hash、提交文字本身的標題及其來源（如有）、主要作品標題（如來源主要解讀一項具名作品）、作者／日期（如有）、正文或原圖、完整度及來源錨點。`primary_work_title` 只有在 canonical source 明示作品名稱，而且整份來源主要圍繞該作品作解讀時才可設定；單純提及、引用或比較作品時必須省略。真源原標題記為 `source_declared`；使用者為自己提供的文字或圖片明示標題時記為 `user_supplied`。兩者都可作主標，但不可互相冒充；AI 擬題只可記為來源衍生標題。對 `user_provided_text`，`source.title`、`source.author`、`source.source_label` 只描述提交的文字本身；未明示的貼文作者不可由提交者或被引用作品作者推定。被引用作品另存於 `source.citation_evidence`，欄位為 `title`、`authors`、`publication`、`original_date`（正文有明示時）、`original_source_url`（HTTPS）及 `original_source_url_status`（核對官方來源後固定為 `verified`）。`source_url` 只代表 URL 擷取身份，`user_provided_text` 不得設定它。編譯器須解析正文可辨認的書目引用並逐欄核對 citation evidence；中文作者分隔符號等非 ASCII 標點必須保留。來源切片以獨立證據列投影原文題名、作者、刊物、日期與 URL，成果頁須同時保留提交文字標題及引用作品題名／來源／連結；不得把被引用作品資料覆寫成提交文字的作者或題名。
   - 固定書目辨識格式為 `作者列〈題名〉，刊於《刊物》，YYYY 年 M 月 D 日`；辨識成功後，`source.citation_evidence` 的題名、作者、刊物、日期均須逐項相符，URL 必須通過 HTTPS 格式檢查並帶 `verified` 狀態，否則 KP 驗證阻止投影。
2. `analysis`: 忠於來源的主張、例子、數字、限制、置信度及不確定性；每項都回指文字或 `IMG-*` 錨點。
3. `coverage_units`: 來源中對使用者有價值、屬內容主題核心或可供日後知識槓桿／疊加的方法、限制、例外、證據、數據、案例、工作法及結構單元；每項必須有處置去向，例如 source claim、主 KP、application、infographic、relation 或明確省略理由。覆蓋深度先按 `source_profile` 決定；一般章節摘要不能取代章內高價值方法或限制的處置。任何處置到 `infographic` 的單元必須同時保存能在 canonical source 逐字讀回的 `source_evidence`，以及已校對的 `infographic_text`；`content_map` 必須使用同一證據及同一讀者文字意圖。只存在於分析、應用或處理紀錄的內容不得進圖，也不得進入成果頁 HTML 的讀者可見或可抽取文字表面。
   Compiler 另由 canonical source 抽取可驗證的結構段、步驟及「Then／Now」等成對轉變，逐項反查 coverage ledger；只因來源分析先漏項而得到的全綠結果必須被拒。
4. `knowledge`: 原子 KP、source-local 關係、證據、覆蓋表與未覆蓋項；不能因篇幅把核心概念靜默刪走。這裡的 `relations` 只代表同一來源內已寫入 knowledge-point record 的關係，不等於跨來源 semantic bridge。
5. `application`: 個人化扣連、採納判斷、品味狀態、複利五類判斷、洞見摘要、所用既有 vault anchor，以及 idea layer 候選。idea 候選包括 concept、hub、tension、taste_signal、application_scene 與 semantic_bridge；候選必須有關係類型與證據錨點，但不得在未覆核前冒充正式 graph link。
6. `infographics`: 每頁鎖定 brief、`content_use`、相容的 `graph_type`、圖型專屬 `visual_structure`、覆蓋 ID、每項覆蓋 ID 對應的確切可見文字、主標及其來源、可見文字、頁面分工、正式 PNG 名稱、編譯雜湊、provider/model/hash/readback。各圖型的 row／step／node 等必填結構只由 `docs/資訊圖生成規格.md` 第 6 節定義；缺失或不相容時不得編譯 prompt。
7. `projection`: source/application/KP/relation/assets/outcome 的預定路徑與同一 `source_identity`；若投影 idea 候選，只可寫入 application note body 或後續整理用候選欄位，不可把 record ID 變成主 graph wikilink。

AI 只產生 KP JSON。Markdown、YAML、registry rows、outcome HTML 與讀者版文字一律由 deterministic projector 從 KP 產生；任何投影不能反過來成為內容真源。

## 5. 資訊圖：完整覆蓋而非強行壓縮

資訊圖是原文的助讀成果，不能為了節省時間把原文主線、概念、限制、數字、例子或關鍵條件壓成一張圖而失真。

- 每張圖都是可離開本專案、直接交給未知第三方閱讀的獨立成果。可見文字只可來自 canonical source 可支持的 coverage unit、忠實短標及必要追溯資料；個人化應用、處理狀態、審核、儲存、發布或其他後台訊息必須留在 KP 的非圖面欄位。
- 頁數由 KP 的重要性覆蓋表和閱讀障礙決定；每頁要有獨立分工，不能重複同一摘要。
- 一個標準入庫包預設可有 **1–5 張**正式圖；超過 5 張時，worker 必須保留完整 KP、標記為「多卷來源」，按閱讀章節分卷後再生成，不能自行刪減。單一執行批次的安全上限為 **8 張**；若仍不足，需在 completion receipt 明確列出後續卷，不可假稱完整覆蓋。
- 每張正式圖必須由 `gpt-image-2` 生成，保留 provider、model、action、PNG hash 與可讀性讀回；HTML／SVG／CSS、fixture、舊圖及 placeholder 不能充當正式圖。
- 若 KP 的 `source.primary_work_title` 有 canonical source 證據，每頁必須保存同值的 `primary_work_title`，prompt compiler 把它逐字放在**最上方主標**；來源頁標題退到追溯資料，本頁焦點只可作較小副標或分區名稱。沒有主要作品時，才依次使用 `source_declared` 的真源原標題、`user_supplied` 的使用者明示標題，最後才由來源分析擬出具體、來源導向的主標。footer 內容、日期與額外文字限制只看 `docs/資訊圖生成規格.md` 及唯一 prompt renderer，不在 worker、skill 或 runbook 另寫一份，也不得寫死任何特定來源、人物、機構、標題或範例內容。
- `compile-infographics` 是正式頁面編譯入口。它先按 `docs/資訊圖生成規格.md` 第 6 節核對 `content_use`、`graph_type` 與圖型專屬 `visual_structure`；多頁圖集還必須通過第 1 頁導讀 gate，首圖要有 `collection_role=orientation`、全文一句話、重要性與讀者可見的後續內容脈絡句，後續頁才逐點拆解。`navigation_text` 只供版面理解與驗收，不得自動進入圖面可見白名單；任何 AI 工作要求、製作方法、驗收語、讀回語或閱讀操作語進入可見文字時，都要在 prompt 寫出前停止。對照圖必須逐列鎖定 `basis/left_text/right_text`，其他圖型亦須具備各自的 step、node、level、question 或 card 結構，缺失便在寫 prompt 前停止。通過後才一次鎖定標題、覆蓋到可見文字的逐項映射、排板、來源錨點、派發生成日期、prompt、正式資產路徑與編譯雜湊；單頁正式檔名為 `<source_id>_<slug>_source.png`，多頁依次為 `<source_id>_<slug>_source_01.png`。`ig-1.png` 等 package 內部編號不得成為 Vault 正式檔名；目錄、slug、來源 ID 與實際暫存檔名的 Windows 路徑上限只依 `docs/URL到輸出模板規格.md` 第 3 節。`generated_on` 固定本次派發批次的 footer 日期；授權及完成時都重新計算編譯結果，標題、文字、結構、頁數、日期或檔名在編譯後漂移即拒絕完成。
- 正常路徑每頁只生成一次；第二次不是自動重試，只可在鎖定內容錯誤、關鍵內容遺漏、語義矛盾、內部流程污染、嚴重裁切或 footer 缺失時，保存圖片讀回證據；使用者授權邊界只見 `docs/資訊圖生成規格.md` 第 10 節。樣式、走位、對齊、間距、比例、方向或其他不影響理解的視覺微差只記錄，不阻塞交付，也不得觸發重生。
- v2 的可執行入口依次為 `compile-infographics`、`imagegen-authorize`、Codex imagegen 以鎖定 prompt 生成一張 PNG、圖片語義讀回及 `imagegen-complete`。action 必須鎖定 `model="gpt-image-2"`、目標畫布 `1024x1536`、品質方向 `high`、expected asset path 及一個 action 只生成一張；本專案不使用 OpenAI API/SDK worker，也不保存 OpenAI 請求編號作為完成條件。正式寫入前發現嚴重內容錯誤／遺漏時，必須用綁定該 PNG SHA-256、prompt SHA-256、失敗代碼、受影響鎖定文字、實際問題及讀者影響的獨立圖片讀回執行 `imagegen-invalidate`；唯一一次 `blocking_repair` 的使用者授權邊界只見 `docs/資訊圖生成規格.md` 第 10 節，普通文字聲稱不能取得修復資格。生成 response 同樣必須附上 Codex imagegen runtime evidence 及綁定實際 PNG 與 prompt 雜湊的 `semantic_readback`，逐項列出實際可見文字及非預期文字。每頁的 compiled-plan hash、prompt hash、asset path、attempt、失敗原因、provider/model、generation request、PNG hash、視覺 QC 及讀回證據都寫入 package-local `imagegen_attempts.json`。`project` 必須拒絕缺帳本、帳本與 KP 不符、一般審美重試、無嚴重內容證據的 repair、已通過頁重生及第三次生成；最終 completion receipt 亦要綁定帳本路徑與 hash。
- 已完成 package 需要修正資訊圖時，只可用 `prepare-infographic-revision` 建立新的 revision KP、資產 slug 及 attempt ledger，再走完整正式路徑。revision 完成後新增 `infographic_current.json` 指向目前版本；原 completion receipt 與原 PNG 逐 byte 保持不變，舊 package 讀回 `superseded_by`。未完成 package 的 `--recover-incomplete` 只可處理明確的 exhausted failed page，且 recovery plan 必須以 `locked_scope` 綁定 canonical source hash、頁數及順序、每頁共享 title/footer，並與既有 ingest execution scope 的來源 metadata 相同。沒有 completion receipt、沒有 pending action、所有該等 shared binding 都未漂移時，已 accepted 的兄弟頁保留原 PNG、prompt、receipt、attempt、job identity 及 first accepted time；只可重開 failed page。此機制不得帶 `source_anchors` 或任何 locked copy/source 變更；要修改來源、copy 或 anchors 必須使用另一條正式 revision 路徑。任一 shared binding 不同即拒絕逐頁 recovery，不能靜默重生整套。此準備步不會生成圖片，正常 initial + 一次 blocking repair 限額不會自動增加。
- 正常來源的生圖授權按資訊圖規格第 10 節判斷；缺少能力或授權時保留 `awaiting_imagegen` checkpoint，明示 `blocked / authorization_missing`，不可假稱完成。本可攜包不自行建立背景排程；執行中的 agent 應沿同一工作完成或留下可恢復阻塞。

## 6. Worker、checkpoint 與完成

worker 的耐久狀態只記執行事實：`accepted`、`queued`、`running`、`awaiting_imagegen`、`blocked`、`committing`、`completed`。`queued` 只表示背景 dispatch 已建立、尚未綁定 worker；綁定後才進入 `running`。`committing` 表示唯一 writer、outcome 或完成 receipt 的可恢復交易尚未完全收束；`worker-readback` 必須返回同一 `project` 工作的驗證及恢復動作，不得另建工作。內容真相仍在 Input Bundle、KP、正式 image receipt 及 Obsidian readback。

可恢復 checkpoint 只有四類：Input Bundle、已驗證 KP、已授權及已消耗的 gpt-image-2 attempt/receipt 帳本、完成 receipt。正式 worker 在 API 呼叫前即保存派發鎖；重啟時可零 API 呼叫讀回已完成 receipt，但遇到已開始而未完成的派發只能停止及人工裁決，不得猜測重叫。其餘步驟只重做缺失部分；不得重新分配 source ID、重抓已驗證內容、重新生成已驗收頁或重寫重複 vault row。

完成只在以下情況成立：KP 通過 checker、所需 PNG 皆有實際 receipt、唯一 writer 已原子寫入、source-local readback／去重／outcome URL 全部對上，並且 `project --write-vault` 在寫出 immutable completion receipt 前已執行成果頁讀者品質審核。單一來源使用 `outcome_page_quality_audit.py --source-id <source_id>`，覆蓋當前 source outcome page、它在 outcome index 的入口及 Obsidian 入口；不讀歷史成果頁或為本篇排序全庫來源。全庫讀者審核只用於另行明確要求的維護或共用讀者機制改動。若回傳 `needs_review`、找不到當前頁、出現重複可見內容、斷橋、內部流程語、空泛 AI 句式或偽完成狀態，worker 停在 `committing / reader_quality_pending`，不得寫 completion receipt。通過時，completion receipt 必須保存帶 `scope` 的精簡 `reader_quality_audit` 摘要；本篇通過不得宣稱全庫通過。任何一項不成立都必須 `blocked`，並附 stage、實際原因、可執行 retry action 與可重用 checkpoint。

## 7. 時間與使用者體驗契約

- 接單：目標 **60 秒內**返回 `job_id`／已接單；由當前 agent 續行同一工作。本版沒有自動背景服務，不把這個目標當成已驗證的非同步能力。
- `article` 正常單頁真源：目標 **4–8 分鐘**；含一至兩張圖的典型文章：**6–10 分鐘**。
- `paper` 公開論文或長技術報告：目標 **20–30 分鐘**；KP 完成後先回報核心 section 覆蓋、預計圖數與慢點，不可因長文自動升級成全庫回歸。
- `full_body` 全文保存：時間以 canonical body 保存、hash 讀回及核心轉化覆蓋為準；若超過 `article` / `paper` 預期，receipt 必須分開記錄「全文保存」與「內容轉化」耗時。
- 三至五張圖或長內容：通常 **10–20 分鐘**；在 KP 完成後須先回報頁數與預計時間。若 profile 已判定為 `paper` 或 `full_body`，以該 profile 的時間說明優先。
- 十分鐘是一般來源的服務目標，不是刪內容或中斷交付的理由。超時仍要交付正確成果，同時在 receipt 記錄哪一段、為何超時（例如 capture、分析、圖片數量、gpt-image-2 等候或修復）。
超時的正確處理是保存 stage timing 與可恢復 checkpoint；不得以多跑 pytest、vault-health、五問提取、全量盤點或治理 doctor 來補償超時。

任何時間報告都必須用 `first_accepted_at → outcome URL` 的實際 wall time；不可用最後一次重試的短 stage 計時冒充全程時間。

### 7.1 快速入庫固定執行軌道

本節是普通單一來源入庫的效率與執行閘唯一規則；QC 情景升級仍由 `docs/分層QC機制.md` 決定。`tools/url_ingest_qc_router.py` 投影本節，`tools/compiler_worker_v2.py` 在正式入口強制執行，不另建人工表單或第二套完成帳本。

1. **最小必要讀取。** 按 router 讀本來源必需真源；本輪已完整讀過且未變的文件直接沿用。資訊圖完整規格讀取義務仍有效。先保存完整來源，再一次整理 coverage／KP／資訊圖計劃。已有足夠來源證據便停止搜尋；額外查證只處理會影響署名、來源完整性或交付正確性的具體缺口。普通入庫不自動變成市場研究、數據事實查核、跨來源探索或歷史治理追查。
2. **一次合格計劃即鎖定。** 頁數按核心內容覆蓋與可讀性決定，不設任意頁數上限，也不因偏好增加封面、衍生主題、視覺候選或另一套方案。填好每個覆蓋單元經審核的 `infographic_text` 後，可用唯讀 `plan-text-template --kp-file <kp>` 產生 `content_map` 與 `visible_text` 草稿；它不改 KP，不會替作者審核句子或決定圖面閱讀順序。正常路徑直接執行 `compile-infographics --kp-file <kp>`：它在寫 prompt／鎖計劃前先執行一次 preflight，失敗時一次列出各頁必填狀態、覆蓋映射、重複文字一致性、圖中文字自足性及原有圖型／來源契約錯誤，並指明欄位、頁面／覆蓋 ID 與修正方向；失敗不寫 prompt、不鎖計劃，也不得授權圖片。只需要唯讀診斷、不準備編譯時才獨立執行 `plan-preflight --kp-file <kp>`，毋須在正常路徑重複執行兩次。`imagegen-authorize` 在任何派發或帳本寫入前再核對同一 preflight，失敗不得授權圖片。第一個通過 `compile-infographics` 的計劃自動把來源身分、canonical hash、接單 profile、coverage hash、頁序與 compiled hashes 鎖進原 job。相同計劃重跑沿用結果；改頁數、改 coverage、換 profile 或重寫已鎖圖文均被拒絕。`imagegen-authorize`、`imagegen-complete`、正式 `project --write-vault` 都要通過同一 execution gate。
3. **例外必須是具體阻塞。** 生圖前發現來源內容錯誤或核心覆蓋遺漏，可作一次 `--plan-repair-file <root-relative-json>`：必含 `previous_scope_sha256`、`failure_code`（只接受 `source_coverage_gap` / `wrong_source_content`）、`coverage_ids`、逐字可追到 canonical source 及受影響單元的 `source_evidence`、`reader_impact`。沒有綁定證據、審美／探索理由、已有圖片 attempt 或第二次計劃修補均停止。修補只可改受影響單元及其頁面；其他頁的內容、版面與相對順序保留，合法增頁引起的頁碼／總頁數重算除外。工具自身契約缺陷仍走既有 bounded contract-defect reopen，且不得改來源、profile、coverage、頁集合或頁面內容。生圖後的內容修復及完成後 revision 沿用第 5 節，不重開舊帳本。不得手改 job／閘紀錄來消除失敗。
4. **四段操作、六個驗收關。** 驗收關仍依次是 Input Bundle、合格 KP／鎖定計劃、逐頁圖片 receipt、唯一 writer 與本篇讀回、當前 outcome 與 immutable completion receipt、本篇新增 idea candidate receipt。操作固定合成四段：① 接單並保存完整來源，一次完成 coverage／KP／圖文計劃；② 執行一次 `compile-infographics`，使用內建 preflight 的完整錯誤報告集中修正，通過才鎖定；③ 每頁依第 5 節各做一次 `imagegen-authorize` → Codex imagegen → 圖片語義讀回及 `imagegen-complete`，已驗收頁直接略過；④ 執行一次 `project --write-vault`，由它完成唯一 writer、本篇讀回、增量 outcome、讀者品質審核及完成 receipt，讀回本篇完成證據後，若 package 缺本篇候選收據，才用 `obsidian_semantic_maintenance.py --new-content-candidates --source-id <source_id> --output-dir <package>/candidate_receipts --write-receipts --check --json` 保存只屬本篇的候選或明確無候選收據。最後一段含兩個命令，但不可用候選收據代替正式完成 receipt。不要因 `project` 已完成內建讀回而例行重跑獨立的本篇 outcome audit；只有讀回缺口或明確錯誤才針對該項補查。

   續行只看原 job 與 package 的 checkpoint：缺 Input Bundle／KP 才補來源與計劃；編譯的 preflight 阻擋便一次修正報告列出的欄位後重跑同一計劃，未通過前不生圖；某頁已有合格 receipt 便跳過，已授權或已派發而結果不明便先核對帳本與實際 PNG，不能猜測重叫 imagegen；嚴重圖片失敗只走第 5 節的證據綁定修復；`committing` 依 `worker-readback` 恢復原 `project`；`completed` 只讀回既有 completion receipt，若本篇候選 receipt 缺失才補候選。候選工具回傳 `needs_review` 時，核對本篇 application 的候選或明確無候選判斷，不得把已寫 receipt 當成通過。沒有圖片授權便停在 `authorization_missing`。每一關通過便前進，只有新改動或具體失敗才重跑受影響檢查；必需成果完成即停止。不得另建總覽、治理 manifest、handoff、控制台、全庫健康檢或補做「更完整」分析來延長普通入庫。
5. **耗時由工具記錄。** 接單 profile 以 `accept-url` / `accept-text` / `accept-image --profile <article|paper|full_body>` 固定，未指定為 `article`。工具按上節既有上界標示 `article` 一至兩頁 600 秒、較長內容 1200 秒、`paper` 1800 秒；`full_body` 保留分段耗時並標為 `not_comparable`，不得藉此聲稱達標。compile／生圖授權回覆與 completion receipt 自動帶 `execution`：原接單時間、整段 wall time、來源準備、KP／計劃、圖片生成與讀回、投影與讀回時間。這些是實際階段間隔，包含 AI 操作及等待，不能稱純模型耗時。超時標 `over_target`，只續行尚未完成的必要步驟並指出耗時階段；不放寬品質、不丟棄成果、不用新工作或重試時間清零。
6. **恢復與可證明邊界。** 新 job 缺 execution contract 即拒絕正式生圖／寫入。升級前尚未完成的 job，只可在原 compiled prompt、canonical hash 及既有 ledger 核對後一次性採納舊計劃，保留原接單時間；缺失的歷史分段時間標不可得，不能要求重生已驗收圖。已完成的舊 receipt 保持原樣讀回。程式閘可攔正式 CLI 入口的計劃漂移及缺紀錄；不能攔模型思考或任意 editor／shell 操作，後者仍受本節固定步驟約束。測試通過只證明閘有效，真實效率是否達標須由下一次已授權入庫的完整耗時證明。

反審失敗也必須能如實保存：`reverse_review_failed` 核對實際 PNG／prompt、檢查者、全文規格讀取憑證、實讀文字及 failed 軸，不能要求失敗圖片先通過「接受圖片」的全文一致性條件。規格憑證的字數接受 JSON 數字或同值字串，規格 hash 仍須相符。它只留下失敗帳本並阻止入庫，不產生 accepted receipt、不自動取得重生資格；修復資格仍依圖像規格第 10 節。

### 7.2 三種來源的程式工作單

正常新入庫由 `tools/ingest_rail.py` 進入，底層仍使用 v2 的同一 job、KP、圖片帳本及唯一 Vault writer。這一層負責固定下一步，並非第二套完成真源；低階 v2 命令保留作相容與指定修復。

工作單的 `needs_input` 表示程式需要 AI 分析、讀圖或調用已授權工具，不等於等待使用者。每份工作單的 `continuation` 明示接手者、續行指令與是否完成；`continue_same_job` 要求 AI 在原授權內完成該步、提交並接續原工作，不得把階段回傳當成任務結束。`reconcile_image_result` 仍須先對帳，不授權重生。只有已讀回完成憑證才可 `deliver_verified_result`；真正的來源、安全、權限或執行環境阻塞才 `report_blocker`。此欄位是交接契約，不保證宿主 AI 自動執行，也不放寬既有閘門；未知動作不得推定可執行。

| 入口 | 固定保存與分支 | 合流條件 |
|---|---|---|
| `start --url <URL>` | 正規化來源身份，按下列 URL 擷取契約保存來源快照及完整可讀正文 | 完整正文及來源快照一致 |
| `start --text-file <UTF-8 原文>` | 保存原始 bytes，另產生只正規化換行及邊緣空白的完整正文；不經網站清理器 | 原始檔 hash、全文逐字比較及 canonical hash 一致 |
| `start --image-file <原圖>` | 先保存 PNG／JPEG／WebP 原圖，再回傳固定解讀表；逐區轉寫、不可讀處標示及第二輪對照原圖 | 原圖身份、尺寸、轉寫、錨點及覆蓋表通過；原圖永久保留，不能用摘要取代 |

文字附使用者現成 PNG 仍是文字來源的相容附件分支，沿用既有直接保存契約，不冒充 Codex 生圖。新 rail 不接收已生成圖片附件、不暗中重生；圖片來源與生成資訊圖是不同資產。PDF／影音未新增為第四種原生來源；符合 URL 擷取契約的 YouTube 字幕仍是 `url` 子類。

新 rail 將全文保存與內容難度分開：文字與 URL 固定 `full_body_required`，圖片固定 `original_image_required`；只選 `article` 或 `paper`，不得用 `full_body` profile 逃避計時。舊工作可維持原 profile 讀回。

1. `start`／`inspect --job-id <id>` 回傳唯一下一步和當前來源。重交同一來源沿用原身份；擷取失敗也保留原 job，可用 `advance` 續接。
2. `prepare_semantic_plan` 提供完整 JSON Schema。機器 schema 唯一實作是 `tools/ingest_plan_contract.py:plan_schema()`，可用 `python -B tools/ingest_rail.py schema` 讀取，其他文件不複製欄位列表。AI 只填分析、逐字證據、知識點、具體跨來源判斷及圖面內容；未知鍵、偽造狀態欄位、危險 ID、過期 input fingerprint、來源不存在的引文及矛盾洞見會被拒絕。無既有筆記證據時保持 `needs_context`，不能自動聲稱有洞見。
3. `submit-plan --job-id <id> --response-file <plan.json>` 先一次回報所有 preflight 問題，再由程式產生身份、正文保存設定、映射、prompt 和計劃鎖。分析內容亦納入鎖定範圍。合法後只回傳本頁一次 Codex imagegen 的工作單。
4. 原圖用 `image-analysis` 交解讀；生成圖用 `image-result` 交真實工具及讀圖證據。工作單提供固定 response template，審閱結果預設為空／pending，不能由程式預填通過。`advance` 只有 `retry_capture` 可按下列契約呼叫擷取 API；不呼叫語義模型或圖片服務。重啟遇到未完成生圖派發只要求對帳，不猜測重叫。
5. 最後一頁合格後，同一 `advance` 自動完成 source-local 寫入、讀回、成果頁、讀者品質檢查、當篇候選整理及完成憑證。候選回條納入 manifest，候選狀態獨立保留；`needs_review` 不代表已建立正式關係。不得要求 AI 額外記得跑候選命令。
   Compiler 投影以 `coverage_ids_json` 原樣傳遞本頁已核對的 `coverage_ids`，writer 不得從編號前綴猜測其身份。明示欄位無效時不採用文字猜測後備；只有未帶此欄位的歷史筆記沿用舊格式讀取。不同編號不能豁免重複正文、相同分工或重複覆蓋檢查。
6. 程式段耗時寫入同一 job 的 `rail_events`；全程仍按原接單時間計算。`rebind-existing-completion` 已退役，不能把檔案存在硬填為通過；恢復用原工作或有來源證據的 revision。

限制：結構、身份、全文保存、階段順序、重複派發及寫入路徑由程式攔截；圖中是否真的漏字、URL 是否還有未載入正文及內容理解是否正確，仍需真實來源／圖片覆核。本機 AI 若同時有任意 shell 和 Vault 寫權，應用層 rail 不是作業系統權限隔離，不能聲稱任意繞過已被物理禁止。需要這種隔離時須另行配置執行帳戶及寫入權限，不能以假 receipt 替代。

#### URL 擷取契約（唯一規則位置）

普通網頁 URL 分支必須先走擷取 API：固定 Firecrawl `/v2/scrape` → Tavily `/extract`；前者取得合格正文即停止，不為比較而重抓。只有 API 不可用、逾時、服務失敗或回應不合格，才可直接下載 HTML 再抽正文。每次服務最多一個請求，API 共用本次擷取時間預算；缺憑證須留下 `unavailable`，不得假稱已呼叫成功。不可由 AI 自選跳過 API。

由 Adam 或任何使用者透過 `brain-url` 提交的 YouTube URL，一律視為提交者已擁有影片或已取得作者明確授權；流程不得為版權／權限再問、阻止或改走另一入口。它仍屬 `url` 來源，先由 `yt-dlp` 讀取影片 metadata，再只保存影片所宣告原始語言的一條現有字幕軌：該語言有人工軌便優先，否則才選同語言自動軌；不得一次下載翻譯軌或以其他語言替代。其後把字幕轉為完整、有時間錨點的正文。不得下載影片、音訊或縮圖，不得使用登入資料、API key 或 ASR fallback；字幕不存在、受限、格式不能完整重播、語言／軌道不一致或完整性不成立時，必須在 Compiler、資訊圖與唯一 writer 前以來源阻塞停止，不能以影片 description、搜尋摘要或網頁殼層代替全文。Input Bundle 必須保存標準化影片 URL／video ID、所選字幕語言與軌道、`yt-dlp` 版本、原始字幕及資料快照的 hash 和可重建的 canonical body；正式來源只可聲稱為「擷取當時的字幕軌」，不可推定畫面或未有字幕的音訊內容。時間錨點只用作定位，不是文章小節；覆蓋審計以人工選出的字幕證據單位為準，不能把每個時碼 cue 強制當作資訊圖章節。

API request 保留全文：Firecrawl 使用 `formats:["markdown"]` 和 `onlyMainContent:true`；Tavily 使用 `urls`、`extract_depth:"advanced"`、`format:"markdown"`，不傳 `query` 或 `chunks_per_source`。原始 API JSON 保存為 `source_capture_api.json`，直接後備才保存 `source_capture.html`；同一 Input Bundle 記錄 provider、逐次嘗試原因、快照路徑／hash 及 canonical hash。API 全部正文只能正規化換行及首尾空白，不能轉成摘要或固定長度截斷；讀回須從保存的回應重抽並逐字比對。

HTTP 成功不等於來源完整：須驗證回應 URL、成功／失敗欄位、正文、標題及來源可讀性，拒絕登入牆、付費牆、只有摘要或不能確認完整的擷取。API／HTML 均仍受全文保存與來源審閱閘約束。擷取器唯一實作是 `tools/firecrawl_obsidian_pipeline.py:capture_url_api_first`，正式入口由 `compiler_worker_v2.py:command_accept_url` 呼叫；SOP、技能與工具索引只引用本節，不各自維護順序或後備規則。

API 語法依據：[Firecrawl Scrape](https://docs.firecrawl.dev/api-reference/endpoint/scrape)、[Tavily Extract](https://docs.tavily.com/documentation/api-reference/endpoint/extract)。MCP 參數只用其實際 schema；REST API 的 `timeout`／`include_usage` 不可直接塞入未提供這些欄位的 MCP 工具。

## 8. 驗收規格

開發完成前，至少須通過以下可推翻驗收：

| 類別 | 必須證明 |
|---|---|
| 輸入與身分 | URL、文字、圖片各一個 fixture；原文／原圖可讀回、hash 不變、重交零重複 |
| KP SSOT | 同一 KP 生成 source、application、KP、relation、asset 與 outcome；任一投影不一致即拒絕完成 |
| 來源忠實 | 文字／圖片 anchors 有覆蓋；placeholder、缺正文／原圖、未覆蓋核心項目及矛盾洞見在 writer 前阻擋 |
| 內容價值覆蓋 | canonical source 的高價值單元都能追到 KP、source claim、application、infographic、relation 或明確省略理由；不得以總章摘要取代章內方法、限制、例外或可槓桿重點 |
| Idea layer / semantic bridge | KP 能分清 record 與 idea：source、KP、APP、registry 是查證層；concept、hub、tension、taste_signal、application_scene 才是 graph 候選。semantic_bridge 候選必須有關係類型、證據錨點及 `需要查證` 狀態；未覆核前不得寫成正式 concept-to-concept link，亦不得用 `KP-...` / `APP-...` wikilink 進主圖 |
| 資訊圖 | 頁數由 coverage 決定；每個 coverage unit 都保存 canonical `source_evidence` 與 reviewed `infographic_text`，再由唯一 `content_map` 映射到圖面讀者文字意圖；漏項、重複分配、未經審核的自由改寫或只列 `coverage_ids` 均被拒。用途／圖型不相容、圖型專屬 row／step／node 等結構缺失、結構文字未進 `visible_text`，都在 prompt 寫出前被拒；對照圖另有左右欄及逐列配對反例。主要作品漏標及比較作品誤升格都有反例。每頁主標有可讀回來源並在 prompt／可見文字白名單／圖片讀回內；標題、來源、作者、日期、數字、評分、頁碼與 footer 必須逐字核對；一般正文卡片允許在不改變意思、不新增主張、不反轉限制下因排版壓縮，並需有語義一致讀回。正式檔名由 compiler 產生；prompt 有第三方自足內容契約；action 鎖定 Codex imagegen / `gpt-image-2`、目標畫布、品質方向與 expected asset；單次派發帳本禁止一般重生與第三次生成；讀回綁定真實 PNG 與 prompt hash。編譯後漂移、缺 attempt ledger、一般審美重試、無嚴重內容錯誤／遺漏證據的 repair、已通過頁重生及第三次生成都在 writer 前被拒 |
| 寫入與讀回 | 只經 `ObsidianVaultAdapter`；source-local readback、同一 source ID、registry 去重、增量 outcome URL 一致 |
| 恢復 | 模擬 capture、KP checker、image receipt、writer 任一中斷後，以同一 job/source identity 恢復且不重複寫入 |
| 執行與跨 runtime | 同一工作保持可恢復；執行器遵守 Input Bundle／KP／receipt，不把 session ID 當內容真源。背景派發須另有真實 runtime 能力證據，不能由本套件文件推定 |
| 時間證據 | 量度整段 wall time，並對 URL、文字、圖片各完成一份真實 UAT；超時原因可讀回，不能以 fixture 或 checkpoint 重跑冒充達標 |

## 9. 可攜部署與停止條件

部署只建立乾淨工作區、工具、規格和空白 Vault，不匯入私人筆記或歷史收據。具體安裝命令由套件入口提供。部署後所有命令以新工作區為 cwd，root-relative 路徑不得解析到工作區外。

離線 fixture 必須標示，不能成為正式來源、圖片或完成憑證。真實 URL、文字、圖片的全程驗收各有自身 source identity；URL 擷取、新生圖及外部能力只在使用當時確認。未有 live 證據時不得聲稱新電腦已通過三入口真實 UAT 或十分鐘 SLA。

`project --write-vault` 已完成後重跑須零寫入讀回；KP、投影、帳本、憑證或成果雜湊漂移即阻擋。`rebind-existing-completion` 已退役，不能把檔案存在硬填成完成。未實作的背景能力不在本套件可用交付範圍。
