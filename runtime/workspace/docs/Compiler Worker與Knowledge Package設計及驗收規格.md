# Compiler Worker 與 Knowledge Package 設計及驗收規格

本文件是可攜「創造型 2nd Brain」的三入口工作、Knowledge Package、編譯、恢復和正式完成唯一 owner。它不保存某台電腦的已通過狀態。先以 `python -B tools/compiler_worker_v2.py capabilities` 讀回程式能力；能力齊備不等於外部 provider、圖片生成或真人 UAT 已通過。

## 1. 決策與邊界

入庫編譯期間，Knowledge Package（KP）是單一內容真源。source note、application、KP notes、relations、PNG 引用與 outcome 只能由同一份已驗證 KP 投影。完成後的 Vault Markdown、registry 與 assets 是正式資料與閱讀/檢索真源；HTML/索引可重建。不得從投影無痕反改 KP 或完成收據。

新來源唯一入口是 `ingest_rail.py`，底層使用 `compiler_worker_v2.py`；正式 Vault writer 仍只有 `ObsidianVaultAdapter`。相容 pipeline 僅用於既有工作修復與文字附現成 PNG 保留，後者完成證據由來源與 writer 規格定義，不冒充 v2。

一條規則只有一個 owner：來源/保存/歸屬/writer 見 [來源規格](URL到輸出模板規格.md)；圖像全文、母版、生成和反審見 [圖像規格](資訊圖生成規格.md)；idea/bridge/問答/整理見 [語義圖規格](Obsidian語義索引與關聯圖落地方案.md)；閱讀投影／目錄／思考紀錄見 [閱讀層](成果頁索引資料層設計.md)；驗收分工見 [驗收](多層驗收機制.md)及[QC路由](分層QC機制.md)。

## 2. 唯一流程

接單 → 不可變 Input Bundle → canonical source → 一次合格 KP/計劃 → 唯一 writer → 本篇讀回 → 增量 outcome/不可變 completion receipt → 本篇 idea candidate receipt。新 `ingest_rail start` 預設 `knowledge_only`，KP 是零張資訊圖且 completion receipt 明示 `not_requested`；明示 `formal_infographic` 與完成後的 `add-infographic --source-id` 才在 writer 前插入每頁 Codex imagegen receipt。補圖入口先交同一來源的正式圖頁工作單；已有子工作只可續跑，已有合格 current 圖只讀回交付。子工作的 input bundle、source 與 projection 一律轉為 `formal_infographic`，但只可綁原 canonical source、已接受的語義內容與父 receipt，不得重抓來源或改寫原文字成果。

同一 agent 沿同一來源工作直到完成、明確阻塞或安全 checkpoint；不要讓使用者重貼多段指令。執行器可遵守同一資料契約，但正式新資訊圖只用 Codex 內建 imagegen/gpt-image-2。本套件沒有自動背景服務，不能把 queued、工具存在或派發要求當成已運行。

## 3. 三類真源與 Input Bundle

每次只接受一條來源，先保存不可變 Input Bundle，再開始 AI 工作。

新 v2 文字與 URL 入庫一律保存完整的可讀來源正文：`source_capture`、canonical body、Knowledge Package 的 `full_body_required`、source note 的「正文保存區」及 Vault 讀回必須連成同一條來源鏈。唯一例外是新的 YouTube 字幕 `knowledge_only` 工作：程式設定 `transcript_archive_required`，完整字幕只保存在工作包的不可變存檔，Vault 只放知識與可由存檔重算的短引述；保存與 writer 閘只見[來源規格 §5.1](URL到輸出模板規格.md#51-正文保存模式)。摘要、TL;DR、知識點與資訊圖只是衍生層，不能取代正文；`article`、`paper` 等 profile 只決定分析覆蓋和速度，不降低保存要求。使用者在對話貼文時，接單前須把貼文全部內容與輸入檔逐段核對；合理的標題與排版整理可保留，但刪段、截尾或改寫成摘要必須停在接單前。URL 擷取、快照與後備路徑只按 §7.2「URL 擷取契約」執行；若不能判定擷取完整，標記 `partial_capture` 並停止正式寫入。

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

1. `source`: 身分、保存模式、原始／canonical hash、提交文字本身的標題及其來源（如有）、主要作品標題（如來源主要解讀一項具名作品）、作者／日期（如有）、正文或原圖、完整度及來源錨點。選填 `retrieval_aliases` 為 0–8 個去空白後 1–80 字元的名稱、跨語言名稱或常見同義說法，不可有換行、`[[`、`]]`、`|`、忽略大小寫的重複或與來源標題相同；只供選材，不投影到讀者可見文字。`primary_work_title` 只有在 canonical source 明示作品名稱，而且整份來源主要圍繞該作品作解讀時才可設定；單純提及、引用或比較作品時必須省略。真源原標題記為 `source_declared`；使用者為自己提供的文字或圖片明示標題時記為 `user_supplied`。兩者都可作主標，但不可互相冒充；AI 擬題只可記為來源衍生標題。對 `user_provided_text`，`source.title`、`source.author`、`source.source_label` 只描述提交的文字本身；未明示的貼文作者不可由提交者或被引用作品作者推定。被引用作品另存於 `source.citation_evidence`，欄位為 `title`、`authors`、`publication`、`original_date`（正文有明示時）、`original_source_url`（HTTPS）及 `original_source_url_status`（核對官方來源後固定為 `verified`）。`source_url` 只代表 URL 擷取身份，`user_provided_text` 不得設定它。編譯器須解析正文可辨認的書目引用並逐欄核對 citation evidence；中文作者分隔符號等非 ASCII 標點必須保留。來源切片以獨立證據列投影原文題名、作者、刊物、日期與 URL，成果頁須同時保留提交文字標題及引用作品題名／來源／連結；不得把被引用作品資料覆寫成提交文字的作者或題名。
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
- 正常來源的生圖授權按資訊圖規格第 10 節判斷；缺少能力或授權時保留 `awaiting_imagegen` checkpoint，明示 `blocked / authorization_missing`，工作單的下一步動作停在 `authorize_initial_image`，不可假稱完成。本可攜包不自行建立背景排程；執行中的 agent 應沿同一工作完成或留下可恢復阻塞。

## 6. Worker、checkpoint 與完成

worker 的耐久狀態只記執行事實：`accepted`、`queued`、`running`、`awaiting_imagegen`、`blocked`、`committing`、`completed`。`queued` 只表示背景 dispatch 已建立、尚未綁定 worker；綁定後才進入 `running`。`committing` 表示唯一 writer、outcome 或完成 receipt 的可恢復交易尚未完全收束；`worker-readback` 必須返回同一 `project` 工作的驗證及恢復動作，不得另建工作。內容真相仍在 Input Bundle、KP、正式 image receipt 及 Obsidian readback。

可恢復 checkpoint 只有四類：Input Bundle、已驗證 KP、已授權及已消耗的 gpt-image-2 attempt/receipt 帳本、完成 receipt。正式 worker 在 API 呼叫前即保存派發鎖；重啟時可零 API 呼叫讀回已完成 receipt，但遇到已開始而未完成的派發只能停止及人工裁決，不得猜測重叫。其餘步驟只重做缺失部分；不得重新分配 source ID、重抓已驗證內容、重新生成已驗收頁或重寫重複 vault row。

完成只在以下情況成立：KP 通過 checker、唯一 writer 已原子寫入、source-local readback／去重／outcome URL 全部對上，並且 `project --write-vault` 在寫出 immutable completion receipt 前已執行成果頁讀者品質審核。`formal_infographic` 或 `add-infographic` 還必須有每張所需 PNG 的實際 receipt；`knowledge_only` 必須有零頁、`not_requested` 與文字成果可讀回，這本身是完整成果，不能把不存在圖片寫成已完成圖片。單一來源使用 `outcome_page_quality_audit.py --source-id <source_id>`，覆蓋當前 source outcome page、它在 outcome index 的入口及 Obsidian 入口；不讀歷史成果頁或為本篇排序全庫來源。全庫讀者審核只用於另行明確要求的維護或共用讀者機制改動。若回傳 `needs_review`、找不到當前頁、出現重複可見內容、斷橋、內部流程語、空泛 AI 句式或偽完成狀態，worker 停在 `committing / reader_quality_pending`，不得寫 completion receipt。通過時，completion receipt 必須保存帶 `scope` 的精簡 `reader_quality_audit` 摘要；本篇通過不得宣稱全庫通過。任何一項不成立都必須 `blocked`，並附 stage、實際原因、可執行 retry action 與可重用 checkpoint。

### 已完成成果的目前修訂與跨平台讀回

完成收據保留當次歷史，不可直接更新舊 hash 消除差異。生成的 Markdown／JSON 可在讀回時重建 LF、CRLF 兩種位元組並比對原收據；只容許換行差異，不修剪空白、不忽略 BOM、不放寬改字或缺字。原始文字擷取、API／字幕擷取、PNG 及其他二進位資料仍逐 byte 校驗。這項相容不需要改寫原文或收據。

已完成內容的授權修訂由 `tools/completion_revisions.py` 保存獨立、不可覆寫的 `completion_revisions/` 紀錄，以原收據、逐檔前後 hash、理由、不可變證據及受保護輸入綁定；透過獨佔鎖及前一指標 hash 更新 `completion_revision_current.json`。普通筆記修訂不得搬移檔案或改寫保存正文。普通筆記修訂中，改寫已完成來源的知識點筆記只可經 `completion_revisions.py` 的唯一寫入口（`rewrite_kp_notes`；回填須先暫存全部筆記，改用同一寫入口的 `stage_kp_notes`、`register_kp_notes`、`restore_kp_notes` 分段呼叫，其餘呼叫方一律用 `rewrite_kp_notes`），不得由其他程式直接寫；符合下文 `project --write-vault` 的 full writer 閘門的綁定現行修訂包為例外。寫入口先判斷每份筆記是否屬於已完成的 v2 來源（`completed_v2_package_for_standard_outcome` 找得到現行完成收據，而收據的 `adapter.knowledge_points` 列有該筆記）；不屬於的照常寫入。屬於的，寫入位元組後，每個受影響的來源接受一份修訂紀錄（該來源已接受的全部改動加上本次的筆記，原雜湊取自原收據，理由為固定理由句，證據為呼叫方先寫好的不可變證據檔），並立即以 `completed_receipt_readback` 讀回，全部在呼叫方持有的 vault 寫入鎖內（寫入口自己不取鎖）。任何一步失敗，本次登記的紀錄與指標全部撤銷、本次寫入的筆記放回原狀，並報 `completed_kp_` 開頭的錯誤碼：`note_invalid`、`open_job`、`package_unresolved`、`note_not_in_receipt`、`revision_unsupported` 在第一個位元組寫入前拒絕；`evidence_invalid`（缺理由、授權、摘要，或證據檔不可讀）在登記前失敗，此時筆記位元組已暫存寫入，由寫入口放回原狀（回填的分段呼叫則由呼叫方以 `restore_kp_notes` 放回）；`note_changed`、`write_failed`、`undo_incomplete` 是寫入後的失敗。已完成來源仍有 `running`、`awaiting_imagegen` 或 `committing` 的舊工作時，閱讀服務的解析器找不到套件，寫入口一律拒絕（`open_job`），不當作未完成來源直接寫；已有目前來源審閱、歷史修圖收據或內容投影證據的修訂，寫入口接不住，須走內容修訂流程；已有內容修訂的筆記可再改寫，最新位元組取代該筆記先前的登記。寫入口不放寬 `validate()`、`completed_receipt_readback` 與 `receipt_artifact_matches`。容許直接寫知識點筆記的只有三類：`tools/completion_revisions.py` 的唯一寫入器、會登記自身已完成來源修訂的內容修訂流程，以及 `obsidian_vault_adapter.py` 的來源寫入器（尚無完成收據的新來源入庫、未完成案例的來源編號重綁，或符合下文 full writer 閘門的綁定現行修訂包）；`tests/test_completed_kp_writer.py` 的靜態檢查為每個可能寫到知識點筆記的寫檔位置列出理由，新增未登記的位置即失敗並指出檔案與行號，其執行期測試證明未經寫入口的直接改寫必使 `receipt_readback_check.py` 失敗。重複複查（`resolve_duplicate_review --write`）、回填、關係欄位刷新與機器 ID wikilink 清理都只經此寫入口：複查先寫不可變的決定收據 `outputs/duplicate_review/<時間>-<kp>.json`（決定、目標、理由、複查人）作證據，再依序寫筆記（含登記與讀回）與 registry，寫入口失敗則兩者皆不改；關係欄位刷新遇已完成來源因無證據而拒絕，wikilink 清理略過已完成來源的筆記。KP／slice 差異須有確切歷史修正證據，或獨立全文重驗的目前內容紀錄；歷史 hash 無法重建時必須明示，不得冒充原次驗收。PNG 只可承接既有歷史修圖收據，新的修圖仍走第 5 節正式路徑。未獲接受的差異繼續阻擋讀回：筆記位元組一變、又沒有被接受的修訂，`completed_receipt_readback` 即以 `completed artifact readback mismatch` 失敗，閱讀服務隨之起不來。

純內容修訂沿用原 job 和唯一 writer，不重建入庫工作。先以目前語義 schema 提交候選；`prepare-content-revision` 只在原 package 的 `completion_revision_evidence/` 保存候選、前後 bytes、交易與審閱範本，不寫正式 Vault。獨立目前來源審閱須接受所有改動的精確 hash，才可 apply；歷史原收據、來源身份、canonical／raw capture、KP IDs／順序、圖片及原接單／耗時保留。可修 KP／slice、來源與 APP 的分析投影、source-local relations 及有證據的派生概念；概念失效、共享節點和手寫保護只依 [語義索引 §7](Obsidian語義索引與關聯圖落地方案.md#7-maps設定與安全)。

正式入口如下；所有檔案參數均為工作區相對路徑，`--authorization` 保存已有的具體授權，不自行產生授權：

```text
python -B tools/brain.py rail prepare-content-revision --job-id <原job> --response-file <候選.json> --authorization "<已批准範圍>" --reason "<本次原因>"
python -B tools/brain.py rail apply-content-revision --job-id <原job> --revision-file <transaction.json> --review-file <已接受審閱.json> --dry-run
python -B tools/brain.py rail apply-content-revision --job-id <原job> --revision-file <transaction.json> --review-file <已接受審閱.json>
```

中斷後只續同一 transaction、候選與審閱。正式目標須仍是已審核的 before／after bytes，否則零寫入停止；正文保護只容許生成 Markdown 的 CRLF→LF 讀回差異，改字、空白改動與 BOM 增刪仍拒絕。已 activated 但 HTML 尚未發布時，由既有 outcome owner 核對同身份、目前內容及 projection chain，恢復同一候選，不建立第二個成果頁帳本。原收據及受保護輸入不因恢復而改 hash。

`project --write-vault` 的 full writer 只可在來源尚無任何 completion receipt 時（首次入庫中斷後重跑亦然），或在 `infographic_revision`／`case_revalidation` 修訂包的 `job_kind`、前手收據 hash、目前接受的前手收據與其鏈均仍相符，且該鏈沒有 knowledge package、source slice 及派生成果頁以外的已接受修訂時寫入；其他已有完成收據的來源一律在任何寫入前停止：非修訂包為 `completed_source_requires_content_revision`，轉往 `prepare-content-revision`；過期或不相符修訂包為 `stale_revision_requires_reprepare`，須從目前完成收據重新準備；full writer 會抹去已接受筆記修訂則為 `revision_would_erase_accepted_notes`，不得重新準備，按其內容要求使用 `add-infographic` 或停止並詢問操作者。已有完成 package 的讀回失敗只會保持阻擋，絕不退回 full write；完成收據或 predecessor chain 任一步不可讀也封閉失敗。

已完成來源的 knowledge-only 補圖子 package 如由舊 full writer 產生，該 writer 會以子 slice 重渲染來源筆記、KP 筆記、概念頁及 registry，令父項已接受正文回落為舊 bytes，且父、子 receipt 均無法讀回。唯一復原入口是對補圖子 job 執行 `prepare-infographic-recovery`：它只推導及暫存，僅在父 package 的 `completion_revision_evidence/recovery-<hash>/` 寫入其 transaction，絕不寫正式 Vault；同一請求重跑會重用同一 transaction，既有 transaction 不相符即以 `infographic recovery staging conflict` 停止。它不會虛構 bytes：每個目標只可為不可變證據中 hash 相符的已接受 bytes、正文不變而只引用補圖子自身 PNG 的圖片 overlay，或由 staged before snapshot 複製的 registry rows；被拒絕的 transaction 只可經其 before snapshot 作證據，絕不可套用。請求必須帶已有而具體的 `--authorization`、具體 `--reason`（各至少 6 字，工具不產生 authorization）及至少一個指向較早 `transaction.json` 的 workspace-relative `--evidence-transaction`。暫存後，須由同一獨立 current-source review 接受完全相同的 hash，才可對父（原）job 以既有 `apply-content-revision` 先 `--dry-run` 再套用；transaction 的 `projection_scope` 為 `infographic_recovery`，套用會透過唯一 CAS writer 寫入 bytes，然後更新父紀錄、子紀錄及子閱讀頁，完成後重跑零寫入。來源 ID 不以數字結尾時，復原 fail closed 拒絕，且不刪除任何資料。

```text
python -B tools/brain.py rail prepare-infographic-recovery --job-id <補圖子job> --evidence-transaction <transaction.json> --authorization "<已批准範圍>" --reason "<本次原因>"
```

目前修訂候選由既有 semantic maintenance owner 只讀該來源的現行 APP 生成，保存於同 transaction 資料夾，綁目前修訂、原 completion 身份及 APP hash；不覆寫原 `candidate_receipts/new_content_candidates.json`。已完成但缺這份派生證據的交易，可用同 apply 補存並如實回報寫入；再次 apply 為零寫入。completed 的 `inspect`／重交同來源只讀已驗證的目前候選；目前證據缺失或不符便停止，不退回歷史快照。候選與本輪修訂耗時不等於原次或新入庫速度，原 `over_target` 保留。

純內容修訂沿用原 job 和唯一 writer，不重建入庫工作。先以目前語義 schema 提交候選；`prepare-content-revision` 只在原 package 的 `completion_revision_evidence/` 保存候選、前後 bytes、交易與審閱範本，不寫正式 Vault。獨立目前來源審閱須接受所有改動的精確 hash，才可 apply；歷史原收據、來源身份、canonical／raw capture、KP IDs／順序、圖片及原接單／耗時保留。可修 KP／slice、來源與 APP 的分析投影、source-local relations 及有證據的派生概念；概念失效、共享節點和手寫保護只依 [語義索引 §7](Obsidian語義索引與關聯圖落地方案.md#7-maps設定與安全)。

正式入口如下；所有檔案參數均為工作區相對路徑，`--authorization` 保存已有的具體授權，不自行產生授權：

```text
python -B tools/brain.py rail prepare-content-revision --job-id <原job> --response-file <候選.json> --authorization "<已批准範圍>" --reason "<本次原因>"
python -B tools/brain.py rail apply-content-revision --job-id <原job> --revision-file <transaction.json> --review-file <已接受審閱.json> --dry-run
python -B tools/brain.py rail apply-content-revision --job-id <原job> --revision-file <transaction.json> --review-file <已接受審閱.json>
```

中斷後只續同一 transaction、候選與審閱。正式目標須仍是已審核的 before／after bytes，否則零寫入停止；正文保護只容許生成 Markdown 的 CRLF→LF 讀回差異，改字、空白改動與 BOM 增刪仍拒絕。已 activated 但 HTML 尚未發布時，由既有 outcome owner 核對同身份、目前內容及 projection chain，恢復同一候選，不建立第二個成果頁帳本。原收據及受保護輸入不因恢復而改 hash。

目前修訂候選由既有 semantic maintenance owner 只讀該來源的現行 APP 生成，保存於同 transaction 資料夾，綁目前修訂、原 completion 身份及 APP hash；不覆寫原 `candidate_receipts/new_content_candidates.json`。已完成但缺這份派生證據的交易，可用同 apply 補存並如實回報寫入；再次 apply 為零寫入。completed 的 `inspect`／重交同來源只讀已驗證的目前候選；目前證據缺失或不符便停止，不退回歷史快照。候選與本輪修訂耗時不等於原次或新入庫速度，原 `over_target` 保留。

標準成果頁須依正式 job 綁定、資訊圖目前指標及已驗證的 case 修訂來源選擇目前收據，不以時間排序。已有完成鏈但無唯一可驗證目前版本時停止，不可退回無收據的直接生成。撤回／待覆核關係不得由舊計劃重播重新設為成立；KP 閱讀、概念筆記及星雲均以 registry 的已成立關係投影。

## 7. 時間與使用者體驗契約

- 接單：目標 **60 秒內**返回 `job_id`／已接單；由當前 agent 續行同一工作。本版沒有自動背景服務，不把這個目標當成已驗證的非同步能力。
- 一般單篇 `knowledge_only` URL／文字／圖片：目標 **180 秒**（contract 名 `normal_knowledge_only_v1`）；這是效率觀察目標，不是中止、截短、拒收正確成果或重設 `first_accepted_at` 的期限。completion receipt 如實標示 `within_target`／`over_target`、各段耗時和最慢可量度段。
- 已接受的 YouTube 字幕 URL：目標 **240 秒**（contract 名 `youtube_caption_knowledge_only_v1`）；只限 Input Bundle 已核對 YouTube URL／video ID、`yt-dlp` 字幕 VTT、metadata snapshot、caption policy 與 canonical body 的單篇 `knowledge_only`。普通 URL 不可選用此目標。
- 內容量分級：有閱讀量紀錄的新工作，實際目標取「合約目標」與「閱讀量級別目標」較大者——short／medium 180 秒、long 240 秒、very_long 360 秒（級別定義見 §7.1 第 5 項）。receipt 同時記 `contract_target_seconds`、`target_seconds`（實際目標）及 `target_basis`；沒有閱讀量紀錄的工作（例如原圖、舊工作）沿用合約目標，舊 receipt 不回填。
- 時間標準分兩段（本條是寬限的唯一規則位置）：上條的實際目標是**核心目標**；receipt 的 `prior_knowledge_effect.status` 為 `candidates_reviewed`（入庫時有舊知識候選，見 §7.2）時，另加「接上舊知識」寬限，實際目標 = 核心目標 + 寬限。寬限是 `tools/compiler_worker_v2.py` 的 `COMPOUNDING_ALLOWANCE_SECONDS`：short／medium 60 秒、long／very_long 90 秒。此時 `execution` 另記 `core_target_seconds`（核心目標）及 `compounding_allowance_seconds`，`target_seconds` 為兩者之和，`target_basis` 以 `+compounding_allowance` 結尾（例如 `contract+compounding_allowance`）；沒有候選、沒有閱讀量紀錄或級別不在表內的工作不帶這兩欄，`execution` 與以前逐位元組相同。`tools/ingest_benchmark.py report` 另列 `core target s` 與 `allowance s` 兩欄，receipt 沒有時顯示 `-`。
- `brain-research` 的整個研究任務不適用 180／240 秒；它不是單篇 `knowledge_only` receipt，不新增研究計時器、總時限或研究片段歸因。
- `article` 的正式資訊圖單頁真源：目標 **4–8 分鐘**；含一至兩張圖的典型文章：**6–10 分鐘**。
- `paper` 公開論文或長技術報告：目標 **20–30 分鐘**；KP 完成後先回報核心 section 覆蓋、預計圖數與慢點，不可因長文自動升級成全庫回歸。
- `full_body` 全文保存：時間以 canonical body 保存、hash 讀回及核心轉化覆蓋為準；若超過 `article` / `paper` 預期，receipt 必須分開記錄「全文保存」與「內容轉化」耗時。
- 三至五張圖或長內容：通常 **10–20 分鐘**；在 KP 完成後須先回報頁數與預計時間。若 profile 已判定為 `paper` 或 `full_body`，以該 profile 的時間說明優先。
- 十分鐘是正式資訊圖路徑（一至兩張圖的一般來源）的服務目標，不是刪內容或中斷交付的理由。超時仍要交付正確成果，同時在 receipt 記錄哪一段、為何超時（例如 capture、分析、圖片數量、gpt-image-2 等候或修復）。

超時的正確處理是保存 stage timing 與可恢復 checkpoint；不得以多跑 pytest、vault-health、五問提取、全量盤點或治理 doctor 來補償超時。

任何時間報告都必須用 `first_accepted_at → outcome URL` 的實際 wall time；不可用最後一次重試的短 stage 計時冒充全程時間。

### 7.1 快速入庫固定執行軌道

本節是普通單一來源入庫的效率與執行閘唯一規則；QC 情景升級仍由 `docs/分層QC機制.md` 決定。`tools/url_ingest_qc_router.py` 投影本節，`tools/compiler_worker_v2.py` 在正式入口強制執行，不另建人工表單或第二套完成帳本。正常 `knowledge_only` 只讀工作單提供的 compact schema、完整來源 block／原圖和必要真源；程式保留全文／原圖、hash、逐字證據、KP ID、coverage ID、既有 source／application／registry／outcome 投影和唯一 writer。AI 必填摘要、知識點及真實 evidence block ID；來源有實質依據時可填 metadata、relations 與 application 的全部既有欄位，沒有既有脈絡才如實 `needs_context`。來源 block 只作定位和完整保存，不等於所有段落已被語義分析或 coverage；但明示編號步驟、Then／Now、案例或階段等有限結構核心必須有語義 block 引用，程式只回報漏掉的 block ID，不回復逐 heading／逐段大表。正式資訊圖規格只適用明示 `formal_infographic` 或後續 `add-infographic`。

若計劃明示 `primary_work_*`，任何新 KP（包括 `knowledge_only`）都必須在正式寫入前通過 canonical source 的逐字與主要解讀關係驗證；錯誤分類不可由補圖入口自動降成 `none`。已完成來源只可經已接受、可讀回的內容修訂更正這三欄，原來源身分、capture 與 completion receipt 不變。

1. **最小必要讀取。** 按 router 讀本來源必需真源；本輪已完整讀過且未變的文件直接沿用。正常 `knowledge_only` 先保存完整來源，再按 compact 工作單作語義分析，不讀資訊圖或工程整包規格；正式資訊圖分支才有完整圖像規格讀取義務。已有足夠來源證據便停止搜尋；額外查證只處理會影響署名、來源完整性或交付正確性的具體缺口。普通入庫不自動變成市場研究、數據事實查核、跨來源探索或歷史治理追查。
2. **明示正式資訊圖才鎖圖面計劃。** 頁數按核心內容覆蓋與可讀性決定，頁數規則只見第 5 節（預設 1–5 張、超過 5 張分卷、單批最多 8 張），不另設其他上限，也不因偏好增加封面、衍生主題、視覺候選或另一套方案。填好每個覆蓋單元經審核的 `infographic_text` 後，可用唯讀 `plan-text-template --kp-file <kp>` 產生由同一 materializer 投影的 `content_map`、`visible_text` 與 `source_anchors` 草稿；它不改 KP，不會替作者審核句子或決定圖面閱讀順序。`check-infographic-plan`、prepare 與編譯都用該投影；舊手填值只可逐項相同，任何衝突仍失敗並使舊 `reader_page_sha256` 失效，絕不自動過關。正式圖面路徑直接執行 `compile-infographics --kp-file <kp>`：它在寫 prompt／鎖計劃前先執行一次 preflight，失敗時一次列出各頁必填狀態、覆蓋映射、重複文字一致性、圖中文字自足性及原有圖型／來源契約錯誤，並指明欄位、頁面／覆蓋 ID 與修正方向；失敗不寫 prompt、不鎖計劃，也不得授權圖片。只需要唯讀診斷、不準備編譯時才獨立執行 `plan-preflight --kp-file <kp>`，毋須在正常路徑重複執行兩次。完成後補圖（`add-infographic`）的工作單以檔案交付：父工作包 `work_order/` 下的完整工作單及 `infographic-plan.draft.json`（程式原樣複製父來源 coverage units 與空白頁面骨架，不預填任何圖面文字或審閱），必讀只有 `docs/資訊圖生成規格.md` 全文，其餘程式執行的規格段落在工作單列明。提交前可用零寫入的 `check-infographic-plan --source-id <id> --response-file <plan.json>`：與正式提交共用同一準備驗證及可在提交前執行的編譯 preflight，逐項指明欄位，並回傳程式計算的讀者頁文字及 `reader_page_sha256`；須有修訂 job 或圖片 action 的檢查仍在編譯／授權時執行。授權與對帳階段只輸出 action envelope、鎖定 prompt 及 `work_order/image-response.draft.json` 的指引，內容與檔案相同；`compile_plan`、各 `authorize_*` 及 `image_result` 失敗亦保存 `attempt` 與 `failure`。補圖首次發出工作單時記一次 `work_order_issued`，修訂收據帶「發出 → 計劃接受 → 編譯」的 `knowledge_and_plan_breakdown`。`imagegen-authorize` 在任何派發或帳本寫入前再核對同一 preflight，失敗不得授權圖片。第一個通過 `compile-infographics` 的計劃自動把來源身分、canonical hash、接單 profile、coverage hash、頁序與 compiled hashes 鎖進原 job。相同計劃重跑沿用結果；改頁數、改 coverage、換 profile 或重寫已鎖圖文均被拒絕。`imagegen-authorize`、`imagegen-complete`、正式 `project --write-vault` 都要通過同一 execution gate。
3. **例外必須是具體阻塞。** 生圖前發現來源內容錯誤或核心覆蓋遺漏，可作一次 `--plan-repair-file <root-relative-json>`：必含 `previous_scope_sha256`、`failure_code`（只接受 `source_coverage_gap` / `wrong_source_content`）、`coverage_ids`、逐字可追到 canonical source 及受影響單元的 `source_evidence`、`reader_impact`。沒有綁定證據、審美／探索理由、已有圖片 attempt 或第二次計劃修補均停止。修補只可改受影響單元及其頁面；其他頁的內容、版面與相對順序保留，合法增頁引起的頁碼／總頁數重算除外。工具自身契約缺陷仍走既有 bounded contract-defect reopen，且不得改來源、profile、coverage、頁集合或頁面內容。生圖後的內容修復及完成後 revision 沿用第 5 節，不重開舊帳本。不得手改 job／閘紀錄來消除失敗。
4. **四段操作、六個驗收關。** 驗收關仍依次是 Input Bundle、合格 KP／鎖定計劃、逐頁圖片 receipt、唯一 writer 與本篇讀回、當前 outcome 與 immutable completion receipt、本篇新增 idea candidate receipt。操作固定合成四段：① 接單並保存完整來源，一次完成 coverage／KP／圖文計劃；② 執行一次 `compile-infographics`，使用內建 preflight 的完整錯誤報告集中修正，通過才鎖定；③ 每頁各走一次第 5 節的可執行入口路徑，已驗收頁直接略過；④ 執行一次 `project --write-vault`，由它完成唯一 writer、本篇讀回、增量 outcome、讀者品質審核、當篇候選及完成 receipt。候選不得代替正式完成 receipt；完成後修訂及目前候選的恢復只見第 6 節。不要因 `project` 已完成內建讀回而例行重跑獨立的本篇 outcome audit；只有讀回缺口或明確錯誤才針對該項補查。

   續行只看原 job 與 package 的 checkpoint：缺 Input Bundle／KP 才補來源與計劃；編譯的 preflight 阻擋便一次修正報告列出的欄位後重跑同一計劃，未通過前不生圖；某頁已有合格 receipt 便跳過，已授權或已派發而結果不明便先核對帳本與實際 PNG，不能猜測重叫 imagegen；嚴重圖片失敗只走第 5 節的證據綁定修復；`committing` 依 `worker-readback` 恢復原 `project`；`completed` 只讀回既有 completion 與第 6 節核對的目前候選。候選工具回傳 `needs_review` 時，核對本篇 application 的候選或明確無候選判斷，不得把已寫 receipt 當成通過。沒有圖片授權便停在 `authorize_initial_image` 這一步，不呼叫 Codex imagegen。每一關通過便前進，只有新改動或具體失敗才重跑受影響檢查；必需成果完成即停止。不得另建總覽、治理 manifest、handoff、控制台、全庫健康檢或補做「更完整」分析來延長普通入庫。
5. **耗時由工具記錄。** 接單 profile 以 `accept-url` / `accept-text` / `accept-image --profile <article|paper|full_body>` 固定，未指定為 `article`。新 compact `knowledge_only` contract 的名稱與目標依第 7 節鎖定。既有 `normal_knowledge_only_v1`／180 秒（包括舊 YouTube 工作）及 legacy receipt 保留原 target／`not_comparable`，不因升級漂移或回填。正式圖面路徑沿用第 7 節上列的分鐘區間（程式取各區間上限，以 600／1200／1800 秒表示：一至兩張圖 600、三張以上 1200、`paper` 1800，見 `tools/url_ingest_qc_router.py` 的 `execution_policy`）。completion receipt 自動帶 `execution`：原接單時間、整段 wall time、來源準備、KP／計劃、圖片生成與讀回（零圖為 `not_applicable`）、投影與讀回時間以及最慢可量度段。這些是實際階段間隔，包含 AI 操作及等待，不能稱純模型耗時。有 `work_order_issued` 事件的新工作另帶 `knowledge_and_plan_breakdown`：接單至發出工作單、發出至首次提交、首次至獲接受提交的秒數及提交／失敗次數（時間取事件的完成或開始時刻，資料不足填 `not_available`），令 AI 段可按讀材料、首稿與修正分開追查；同一事件另記閱讀量（block 數、字數、閱讀檔數、目錄版本及 `volume_class`：short < 10,000、medium < 40,000、long < 100,000、very_long ≥ 100,000 閱讀字元），receipt 以 `reading_volume` 投影，令不同長度的耗時可比較；舊 receipt 不回填。工作單指標亦帶操作約束：不讀規格、不跑 doctor／測試、不寫交接或進度卡，完成即回報收據的 `execution`。超時只標 `over_target`，仍續行尚未完成的必要步驟並指出耗時階段；不放寬品質、不丟棄成果、不用新工作或重試時間清零。
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
2. 正常 `knowledge_only` 的 `prepare_semantic_plan` 提供 compact schema、完整且 stable-ID 的來源 block 及 input fingerprint；AI 必填摘要、知識點和證據 block ID，並須處理工作單內明示編號步驟、Then／Now、案例或階段等有限結構核心。來源有實質支持時可帶 metadata、relations 和完整 application 欄位。程式編譯機械 IDs、逐字 evidence、coverage 和保守的無 context 值；不把保存的每段正文冒充 AI 已分析。漏掉核心時只列本 job 的 source block ID 供補分析。formal／case 用完整 schema；兩種新工作單均由 `tools/ingest_plan_contract.py:semantic_response_schema()` 要求下列共同判斷，舊保存計劃保守相容，不漂移舊引用。

   新文字／URL 引用以 `source_catalog_version=source_block_catalog_v2` 綁定。`SPAN-<start>-<end>` 是 canonical source 按 Python 文字讀回、換行為 LF 並移除末端 LF 後的字元位置；零起點、end 不包含在內，精確文字為 `canonical[start:end]`。繁簡中文及非 BMP 字元都按 Unicode code point 計一個，不得直接使用 JavaScript UTF-16 offset。字幕保留完整 cue、時間戳及相鄰上下文，普通文字按完整句引用；需要時一起引用相鄰 SPAN，不把整篇重抄給每個 KP／claim／relation。舊 `SRC-*` 定義不變；legacy 目錄只供內部相容，日常工作單不重複供應兩份全文。圖片仍用已接受的 `IMG-*`，不套文字 offset。input fingerprint 同時綁輸入及版本化目錄，helper 為 `input_fingerprint(package, <job 的目錄版本>)`；不符版本、位置、fingerprint 或逐字證據即拒絕，不能改 canonical／VTT／原始 hash 遷就引用。

   已發出 v2／v3 的工作維持原目錄，不漂移；新文字／URL 工作使用 `source_block_catalog_v4`。v4 的 block ID、文字及次序與 v3 完全相同，只為一般 canonical Markdown 的每個 block 加 `kind`（`heading`／`text`）及 `heading_level`（heading 為 1–6，否則為 `null`）。只有 block 起點所在的實際 Markdown ATX 行以行首 1–6 個 `#` 加空格時才是 heading；字體、粗體、編號、`#標題`、行內 hashtag 及 fenced code 都不是 heading。若 SPAN 不是整行，仍以其起點行判斷。YouTube 字幕存檔及圖片目錄不加這兩個欄位，原有行為不變。v3 對一般文字與網頁與 v2 相同（使用者貼上、外觀像字幕的文字亦同）；只有 `transcript_archive_required` 的 YouTube 字幕存檔改由 `youtube_transcript_capture.caption_reading_chunks` 產生閱讀 block：先去除滾動字幕的重複部分（只移除已出現的文字，任何 cue 文字都不可遺失），再按句末或約 240 字元分組；每個 block 的 `SPAN-<start>-<end>` 指向 canonical 中貢獻 cue 行的範圍，文字以 `[hh:mm:ss]` 開頭。AI 只選 block，引述文字由程式組成，結尾附一次 `https://youtu.be/<影片ID>?t=<秒數>`。每個知識點或關係最多 4 個 block、渲染後最多 700 字元，超過以 `evidence_quote_too_long` 拒絕並列出位置。

   同一輪分析的每個 `knowledge` 項目須交 `reusable_concept`：`status` 為 `reusable`／`not_reusable`／`needs_review`，三者均要具體 `reason`；`reusable` 另要分析得出的 `name`。KP 的 `title` 和概念名稱分開保存；缺判斷的舊 plan 不自動由 title／claim 猜概念，也不把缺值當明確無候選。此共同欄位貫通 compact／full、原圖、research／case 的 KP、slice、writer 及日後再生；各分支原有擷取、授權與研究資格證據仍獨立保留，不增加每點或每日模型回合。概念與橋接的語義責任見 [語義索引 §2](Obsidian語義索引與關聯圖落地方案.md#2-idea-類型與語義橋接)。

   **來源命名結構（`structure_decisions`；欄位、候選判定與 check-plan 的唯一規則位置）。** 原圖按錨點順序，每個 `kind: title` 到下一個 `title` 前的錨點；v4 文字／URL 則由 heading block 起至下一個 level 不高於它的 heading 前的全部 block，若被至少 `STRUCTURE_CANDIDATE_MIN_MEMBERS` 個 knowledge points 引用，便是候選（H3 的 block 因此也屬其 H2 範圍）。來源標題同名（忽略大小寫、全半形及前後標點）的 heading，以及全文唯一而且第一個 block 的 H1，不是候選。每個候選必須二選一：加入一個引用 title／heading 的父 knowledge point，在其 `title` 或 `claim` 保留來源命名，並透過既有 `relations` 連到每個成員；或交 `structure_decisions[]` 的 `{block_id, decision: "not_a_named_structure", reason}`（理由至少有幾個有意義字元）。單純引用 title 作上下文不等於選定父項；有理由的 dismissal 一律可用。`check-plan`／`submit-plan` 以一次 `named_structure_decision_missing` 列出全部未決候選；已接受的 image analysis coverage 不在語義階段重驗或要求改寫。此規則只套用新計劃；既有完成 package 不回溯重驗。

   **舊知識連結（`prior_knowledge`；欄位、錯誤碼、工作單檔與 registry 寫入的唯一規則位置）。** 新來源入庫時，程式在同一輪分析提供相關的舊概念及其 linked 舊知識點，由 AI 決定新知識對舊知識的作用，不增加模型回合。

   1. 檢索：`tools/obsidian_idea_retrieval.py:related_prior_candidates`（評分語義、門檻與校準依據見 [語義索引 §2「舊知識候選的評分」](Obsidian語義索引與關聯圖落地方案.md#舊知識候選的評分)，此處不重複）。候選有兩種 `kind`：`concept`（有效概念，排除只屬本來源或沒有 linked KP 的概念；每個概念最多帶 2 個 linked 舊 KP：`kp_uid`、`claim`、來源編號）與 `kp`（未歸入概念的知識點：linked、不屬本來源，且不被任何有效概念筆記的 `knowledge_point_records` 收錄的單一 KP；`concept_name` 與 `concept_path` 為空，`prior_kps` 就是該 KP 本身；被概念收錄的 KP 只經概念出現，不重複成為候選）。每條查詢取概念前 3 個與 `kind: kp` 前 2 個（兩種各自排名、互不擠出），合併去重；RC、RK 每份清單最多 8 個概念加 4 個 `kind: kp`。候選分兩部分，互不改號：工作單候選 `RC-01`…（只用工作單查詢）；計劃層新增候選 `RK-01`…（只用草稿各 KP 的 claim 與概念名，不含已在 RC 的概念或知識點）。分數達 `PRIOR_LINK_MIN_SCORE` 者為 `above_threshold`，只作強度標示（工作單標題的 ABOVE THRESHOLD／below threshold）；要否交代的規則只見下面第 4 項的 `prior_knowledge_decision_missing`，門檻與名次都不能豁免候選。`must_decide` 由程式從候選清單即時推算（`must_decide_flags`：分數大於 0 者為 true，等於全部候選），不存入 `prior_knowledge_rc`。RC 在工作單首次發出時存入 job 的 `prior_knowledge_rc`：`candidates` 是完整 RC 清單（含 `kind`、分數、`above_threshold` 與 `prior_kps`；沒有候選時為空清單），`registry_fingerprint` 是當時 `_registry/knowledge_points.jsonl` 與 `sources.jsonl` 的雜湊（只作稽核證據，不含 `concepts/` 筆記）。check 與 submit 直接讀回這份 RC，不重算，所以中途有其他來源入庫，RC 也不會改變；RK 仍按計劃即時計算。沒有 `prior_knowledge_rc` 的舊 job 退回以工作單查詢即時重算 RC，也不會事後補存。
   2. 工作單：首次發出時以來源標題、小標題及 YouTube 字幕分段首句作查詢，不用正文。有候選才寫 `work_order/related-knowledge.md`（檔頭載明決定規則：每個候選的交代規則（見第 4 項；check-plan 會依計劃的主張加入 RK）；理由相同時，一條 `not_linked` 可在 `reason` 點名最多 `PRIOR_DISMISS_GROUP_MAX` 個 `candidate_id`，但 `reason` 要說出它們談甚麼、為何不連；`no_meaningful_link` 表示全部候選都已用 `not_linked` 交代、沒有連結；以及 `concept_relation` 規則。其後為候選編號、概念原名、分數與強度標示（標題寫 `ABOVE THRESHOLD` 或 `below threshold`，不再有 `must_decide` 標記；交代規則見第 4 項）、舊 KP。全部候選一律列出，不因字數省略；單個舊 KP 的 claim 超過 `RELATED_CLAIM_CHARS` 字時才截斷；`kind: kp` 的候選標題寫成「未歸入概念的知識點（來源 <來源編號>）」，下附該 KP；檔尾一行只列概念候選的名稱，標題為「以下概念名已存在於知識庫（可直接沿用，沿用時才需填 `reusable_concept.understanding_delta`；新概念名不要填）」，沒有概念候選時寫「（沒有）」），指標帶 `related_knowledge_file`，草稿預填空的 `prior_knowledge`；沒有候選便不建檔、不預填。
   3. 欄位（compact 計劃的可選頂層物件）：`decision`（`linked`／`no_meaningful_link`）、`reason`（選填，只作總結，不算覆蓋任何候選）、`cross_links[]`（`source_index`、`target_kp_uid`、`relation_type`、`insight`、`evidence_block_ids`；`relation_type` 為 `supports` 或 `extends` 時另必填 `concept_relation`：`same_concept` 或 `distinct`，`distinct` 另附一句 `concept_relation_reason`；目標是 `kind: kp` 的候選時只可填 `distinct`，理由可寫「目標尚未歸入概念」）、`not_linked[]`（`target_kp_uid`、`reason`）；`reusable_concept` 另可帶 `understanding_delta`。`relation_type` 與新工作單的同源 `relations[].relation` 共用詞表 `RELATION_TYPES`（`tools/ingest_plan_contract.py`；語義見語義索引 §2）。未交 `prior_knowledge` 的舊計劃與空 vault 照常通過，空白範本等同省略。
   4. 驗證：`check-plan` 與 `submit-plan` 共用 `validate_plan`，RC 讀回 job（見第 1 項）、RK 即時計算，check 輸出在有候選時多帶 `related_candidates`（RC 在前、RK 在後；每項另帶 `must_decide`，欄位保留以維持 schema 穩定，值見 `prior_knowledge_decision_missing`）。錯誤碼沿用下一項 check-plan 的條數與截斷規則，每項指出欄位與修正方向：
      - `prior_knowledge_decision_missing`：有列出的候選（RC 與 RK 全部，`must_decide` 一律為 true）沒有有效決定（`decision` 不是 `linked` 或 `no_meaningful_link`；只帶 `cross_links` 或 `not_linked` 不算），或有候選沒有被交代。兩種決定用同一套覆蓋規則（`concept` 與 `kp` 一律適用）：每個候選都要有一條 `cross_links` 指向它任一舊 kp_uid，或一條 `not_linked` 點名它；點名的方式是 `target_kp_uid` 等於其 `candidate_id` 或任一 kp_uid，或在該條 `reason` 內寫出 `candidate_id` 或 kp_uid。頂層 `reason` 點名候選不算覆蓋。`no_meaningful_link` 的意思是全部候選都已用 `not_linked` 覆蓋，而且 `cross_links` 為空（頂層 `reason` 選填，只作總結）；決定與連結數矛盾的情況見 `prior_knowledge_decision_conflict`。錯誤訊息按此規則列出尚未被交代的候選，不提門檻或名次。
      - `prior_knowledge_dismiss_group_too_large`：一條 `not_linked` 可同時覆蓋理由相同的多個候選，但覆蓋數（`target_kp_uid` 加 `reason` 內點名的不同候選）最多 `PRIOR_DISMISS_GROUP_MAX`（`tools/ingest_rail.py` 的具名常數，定為 6）；超出時以此碼拒絕，指出是哪一條及其點名的候選。
      - `prior_knowledge_dismiss_reason_thin`：每條 `not_linked` 的 `reason` 須寫出這些候選談甚麼、為何不連。檢查方法：從 `reason` 刪去所有 `candidate_id`（`R[CK]-\d+`）、本次候選的 kp_uid、標點及空白，剩下的字元數不得少於 `PRIOR_DISMISS_REASON_MIN_CHARS`（具名常數，定為 6）；未達時以此碼拒絕，訊息指出是哪一條，以及要補寫甚麼。這只擋空泛理由，不判斷理由是否正確。
      - `prior_knowledge_decision_conflict`：`linked` 卻沒有連結，或 `no_meaningful_link` 卻帶連結。
      - `cross_link_target_not_offered`（目標不在 RC 與 RK 任一部分的舊 KP 內；工作單印出過的目標必在其內）、`cross_link_self_source`（目標屬本來源）、`relation_type_invalid`、`cross_link_duplicate`、`cross_link_limit`（超過安全上限 12 條）。
      - `cross_link_evidence_invalid`：新方 `evidence_block_ids` 缺漏或不屬本來源；舊方依據是目標 KP 本身的原證據，不另要求逐字引文。
      - `cross_link_insight_paraphrase`：`insight` 與任一方 claim 的字詞重疊達 `INSIGHT_PARAPHRASE_OVERLAP`（中文按字元 bigram、英文按詞）。`cross_link_insight_generic`：沿用成果頁品質審核的泛論與套話詞表。
      - `concept_name_near_duplicate`：新概念名經 NFKC、casefold、去空白標點後與既有概念名相同，卻不是原名（只比對概念，`kind: kp` 的候選不參與）。`understanding_delta_missing`：所選概念名已存在於 vault 卻沒有 `understanding_delta`。`understanding_delta_unexpected`：概念名在 check 或 submit 當時不存在於 vault，卻填了 `understanding_delta`（新概念沒有舊理解可修正）。
      - `concept_relation_missing`：`supports` 或 `extends` 的連結缺 `concept_relation`，或 `distinct` 缺 `concept_relation_reason`。`concept_relation_name_mismatch`：`same_concept` 但該 KP 的 `reusable_concept` 不是 `reusable`，或其 `name` 與目標 KP 所屬概念名不完全相同；目標是 `kind: kp` 的候選（尚未歸入概念，沒有概念名可對）卻填 `same_concept`，也用此碼，修正方向是改填 `distinct`。
      - `has_insight_without_cross_source`：application 為 `has_insight` 卻沒有本次通過驗證的跨來源關係，改用 `needs_context`。只套用於 compact 新計劃；writer 的 `compounding_metadata_issues` 以同一函式再核對。
   5. 寫入：驗證過的 `cross_links` 以 `scope: cross_source` 寫入 `_registry/relations.jsonl`，沿用既有欄位並增 `target_source_id`、`insight`、`job_id`；`relation` 填詞表值，`evidence` 為新方原文，`relation_id = "rel:" + sha256(source_kp_uid|target_kp_uid|relation)`。只有這條路徑可放寬「只限同源」；writer 另核對新方引文可在來源定位，且目標 KP 仍是 linked 並不屬本來源，否則寫入前停止。同一 `relation_id` 內容相同不重寫；已 `superseded`／`withdrawn` 的行不因重播復活；內容修訂會把沒有再交的跨來源行一併標 `superseded`。`understanding_delta` 連同 `concept_name`、`job_id` 寫入新 KP 在 `_registry/knowledge_points.jsonl` 的行。
   6. 程式能強制的是結構、詞表、目標真實且在候選內、不可自連、證據可定位、`insight` 不只改寫或套話、`same_concept` 的名稱完全相同、可重跑與已撤回的關係不復活（唯一定義，語義索引的入庫與回填段落引用此項）；洞見是否有價值、兩個概念是否真的同一個，程式無法逐條證明，只能抽查，抽查結果不由程式聲稱。
   7. 計劃鎖定：KP 帶 `prior_knowledge` 時，`knowledge_execution_scope` 與 `ingest_plan_scope` 的 `semantic_content_sha256` 另含該物件；不帶此欄位的舊 KP 雜湊不變。圖面修訂的語義雜湊 `revision_semantic_content_sha256` 不納入：修訂計劃只能改圖面、覆蓋與來源補充，複製自父 KP 的 `prior_knowledge` 不會漂移，納入只會令已保存的修訂 origin 雜湊失效。
   8. 入庫回報（欄位的唯一規則位置）：`submit-plan` 在有舊知識候選時把候選數、過門檻數、必須交代數，以及該計劃可重用概念名中「當時已存在於 vault 的名稱」記在 job 的 `prior_knowledge_offer`（`candidates_offered`、`above_threshold`、`must_decide`、`existing_concept_names`；check-plan 不記）。完成收據與 `execution` 同層帶 `prior_knowledge_effect`，入庫指令的完成輸出同帶此欄位；只含計數與逐條連結，不含來源全文。沒有候選為 `{"status": "no_prior_candidates"}`；否則為 `status`（`candidates_reviewed`）、`decision`、`candidates_offered`、`above_threshold`（過門檻個數，意義不變）、`must_decide`（必須交代個數；新 job 恆等於 `candidates_offered`；沒有此欄的舊 job 讀作 0）、`not_linked`（`not_linked` 項數）、`relation_counts`（詞表六值各一個數）、`new_concepts`、`merged_concepts`（名稱在 `existing_concept_names` 內的可重用概念名為併入既有概念，其餘為新概念；沒有 `existing_concept_names` 的舊 job 退回「帶 `understanding_delta`」計算）、`check_plan_rounds`、`submit_plan_attempts`（job 內 `submit_plan` 事件數）、`links[]`（`relation_type`、`insight`、`target_source_id`）。`check_plan_rounds` 是被接受的提交之前 check-plan 的執行次數：check-plan 不寫 job、事件、成果包或 vault，只在計劃未鎖定時於 `work_order/check_attempts.jsonl` 追加一行診斷記錄（時間、結果、錯誤碼），`submit-plan` 把累計摘要記入其 `submit_plan` 事件的 `check_attempts`；`execution.knowledge_and_plan_breakdown` 同帶 `check_plan_rounds`、`failed_check_plan_rounds` 與 `check_error_codes`（錯誤碼＝錯誤訊息冒號前的代碼）。此記錄之前的舊 job 為 `not_recorded`。成果頁的讀者區塊見 [成果頁索引資料層設計 §2](成果頁索引資料層設計.md#2-來源成果頁)。

   新 v4 計劃的 `application.knowledge_use` 是必填決定，值必須在 `VALID_KNOWLEDGE_USE`；缺少以 `knowledge_use_decision_missing` 列出可選值，不能由程式預設為「事實真源」。既有完成來源、KP JSON 與收據不回填或重驗。個人判斷沿用 `application`：可傳 `personal_relevance`（高／中／低／未知）及 `personal_relevance_reason`；非未知須有已核對的 `context_anchors` 和理由，未提供則保守未知。缺既有脈絡保持 `needs_context`，不固定填高相關、可應用或泛用場景；APP／KP 投影欄位見 [來源與 writer §7–8](URL到輸出模板規格.md#7-obsidian-ready-來源欄位)。未知鍵、空分析、危險 ID、過期 fingerprint、漂移引用及矛盾洞見仍由共用驗證拒絕。
   `knowledge_only` 的語義工作單由程式以檔案交付，不把全文印到命令輸出：完整工作單寫入 `<package>/work_order/prepare_semantic_plan.json`（不內嵌來源 block），來源 block 按次序寫入 `reading-NN.md`（每檔最多 12,000 字元、block 不跨檔），`plan.draft.json` 預填版本、目錄版本與 fingerprint，不預填可選段落（有舊知識候選時只預填空的 `prior_knowledge`，見上）。`start`／`inspect`／`advance` 只回傳不超過約 4,000 字元的指標（檔案位置、block 數、字數、check 與 submit 命令）。閱讀檔每行為 `<block ID>`、Tab、block 文字。首次發出時記一次 `work_order_issued` 事件，所以未完成 job 的首次 `inspect` 會寫入這些工作單檔及事件；之後同內容不重寫，已存在的草稿不覆寫，已完成 job 的 `inspect` 仍零寫入。
3. 提交前可用零寫入的 `check-plan --job-id <id> --response-file <plan.json>`：與 `submit-plan` 共用同一驗證（包括 `evidence_quote_too_long`），回傳 `passed`（exit 0）或 `failed`（exit 1）及錯誤清單（最多列 50 項、每項最多 500 字元，並附 `error_count` 總數；有舊知識候選時另帶 `related_candidates`；每個位置的所有未知 `evidence_block_ids` 會在同一次檢查各列一行）；不寫 job、事件、計劃或 Vault，只在計劃未鎖定時追加診斷記錄。`submit-plan` 或 `image-analysis` 失敗時，同一事件保存 `attempt` 及 `failure`（例外類型及最多 500 字元訊息）。`submit-plan --job-id <id> --response-file <plan.json>` 先一次回報所有 preflight 問題，再由程式產生身份、正文保存設定、映射和計劃鎖。新 `knowledge_only` 合法後直接進唯一 writer，零頁以 `not_requested` 完成，不建立待補圖；semantic submit 的成功或失敗寫在同一 job 的 `rail_events`，不另建第二條 pipeline 或計時帳本；正式圖片分支才額外產生 prompt 和一次 Codex imagegen 工作單。已完成文字成果的 `add-infographic` 只可在子 KP 對原 coverage 加 `infographic` 處置及已審核 `infographic_text`；父來源、ID、原文、證據、非圖面處置和完成憑證不可變。
4. 原圖用 `image-analysis` 先交解讀；解讀接納後，下一份工作單才列出已接受 IMG block、舊知識候選的取得方法及 semantic plan；RK 由語義草稿即時計算。正常 `knowledge_only` 仍相容接受同一份回應同時交 image analysis 與 compact semantic plan，後者只引用本次 IMG-* 錨點並由程式在解讀接納後補 input fingerprint。原圖解讀接納而語義失敗時保留原圖、解讀和 IMG block，下一份工作單只修語義，不重讀原圖。只有正式生成圖才用 `image-result` 交真實工具及讀圖證據。工作單提供固定 response template，審閱結果預設為空／pending，不能由程式預填通過。`advance` 只有 `retry_capture` 可按下列契約呼叫擷取 API；不呼叫語義模型或圖片服務。重啟遇到未完成生圖派發只要求對帳，不猜測重叫。
5. `knowledge_only` 的語義計劃合格後，同一 `advance` 自動完成 source-local 寫入、讀回、成果頁、讀者品質檢查、當篇候選整理及完成憑證；正式圖片分支則在最後一頁合格後走同一完成路徑。候選回條納入 manifest，候選狀態獨立保留；`needs_review` 不代表已建立正式關係。不得要求 AI 額外記得跑候選命令。
   Compiler 投影以 `coverage_ids_json` 原樣傳遞本頁已核對的 `coverage_ids`，writer 不得從編號前綴猜測其身份。明示欄位無效時不採用文字猜測後備；只有未帶此欄位的歷史筆記沿用舊格式讀取。不同編號不能豁免重複正文、相同分工或重複覆蓋檢查。
6. 程式段耗時寫入同一 job 的 `rail_events`；全程仍按原接單時間計算。`rebind-existing-completion` 已退役，不能把檔案存在硬填為通過；恢復用原工作或有來源證據的 revision。

限制：結構、身份、全文保存、階段順序、重複派發及寫入路徑由程式攔截；圖中是否真的漏字、URL 是否還有未載入正文及內容理解是否正確，仍需真實來源／圖片覆核。本機 AI 若同時有任意 shell 和 Vault 寫權，應用層 rail 不是作業系統權限隔離，不能聲稱任意繞過已被物理禁止。需要這種隔離時須另行配置執行帳戶及寫入權限，不能以假 receipt 替代。

#### URL 擷取契約（唯一規則位置）

普通網頁 URL 分支必須先走擷取 API：固定 Firecrawl `/v2/scrape` → Tavily `/extract`；前者取得合格正文即停止，不為比較而重抓。只有 API 不可用、逾時、服務失敗或回應不合格，才可直接下載 HTML 再抽正文。每次服務最多一個請求，API 共用本次擷取時間預算；缺憑證須留下 `unavailable`，不得假稱已呼叫成功。不可由 AI 自選跳過 API。

使用者透過 `brain-url` 提交的 YouTube URL，一律視為提交者已擁有影片或已取得作者明確授權；流程不得為版權／權限再問、阻止或改走另一入口。它仍屬 `url` 來源，先由 `yt-dlp` 讀取影片 metadata，再只保存影片所宣告原始語言的一條現有字幕軌：該語言有人工軌便優先，否則才選同語言自動軌；自動軌先選 metadata 宣告的同語言 `*-orig`，否則只可選有 URL、實際語言及無 `tlang` 證據的一個具體 VTT item。不得把同語言群組整體交給下載器任選、把繁簡字形互換、一次下載翻譯軌或以其他語言替代。其後把該 item 的字幕轉為完整、有時間錨點的正文。不得下載影片、音訊或縮圖，不得使用登入資料、API key 或 ASR fallback；字幕不存在、受限、格式不能完整重播、語言／軌道不一致或完整性不成立時，必須在 Compiler、資訊圖與唯一 writer 前以來源阻塞停止，不能以影片 description、搜尋摘要或網頁殼層代替全文。Input Bundle 必須保存標準化影片 URL／video ID、所選字幕語言與軌道、group key、item index、非翻譯證據、`yt-dlp` 版本、原始字幕及資料快照的 hash 和可重建的 canonical body；失敗只記失敗 stage、實際 exit code 和不含 URL／查詢／本機私人路徑的短摘要。正式來源只可聲稱為「擷取當時的字幕軌」，不可推定畫面或未有字幕的音訊內容。`knowledge_only` 字幕工作由程式設定 `transcript_archive_required`：上述原始字幕與 canonical body 是工作包存檔，completion receipt 另存 `transcript_archive`（兩個雜湊、存檔路徑及 `verified`），Vault 不放逐字稿；正式資訊圖分支仍為 `full_body_required`。字幕存檔來源不接受完成後的 `add-infographic`（正式圖面需要可逐字讀回的來源正文），入口在任何寫入前以 `blocked` 回覆。寫入閘只攔截行首的字幕時碼行；程式組成的引述不含時碼，AI 自由文字中夾在行中的時碼不屬此閘範圍。時間錨點只用作定位，不是文章小節；覆蓋審計以人工選出的字幕證據單位為準，不能把每個時碼 cue 強制當作資訊圖章節。

API request 保留全文：Firecrawl 使用 `formats:["markdown"]` 和 `onlyMainContent:true`；Tavily 使用 `urls`、`extract_depth:"advanced"`、`format:"markdown"`，不傳 `query` 或 `chunks_per_source`。原始 API JSON 保存為 `source_capture_api.json`，直接後備才保存 `source_capture.html`；同一 Input Bundle 記錄 provider、逐次嘗試原因、快照路徑／hash 及 canonical hash。API 全部正文只能正規化換行及首尾空白，不能轉成摘要或固定長度截斷；讀回須從保存的回應重抽並逐字比對。

HTTP 成功不等於來源完整：須驗證回應 URL、成功／失敗欄位、正文、標題及來源可讀性，拒絕登入牆、付費牆、只有摘要或不能確認完整的擷取。API／HTML 均仍受全文保存與來源審閱閘約束。擷取器唯一實作是 `tools/firecrawl_obsidian_pipeline.py:capture_url_api_first` 的一般 API-first 路線和 `compiler_worker_v2.py:command_accept_url` 的已核准 direct-only 路線；`ingest_rail.py` 負責把模式鎖到同一 job。SOP、技能與工具索引只引用本節，不各自維護順序或後備規則。

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
| 資訊圖 | 頁數由 coverage 決定；每個 coverage unit 都保存 canonical `source_evidence` 與 reviewed `infographic_text`，再由唯一 `content_map` 映射到圖面讀者文字意圖；漏項、重複分配、未經審核的自由改寫或只列 `coverage_ids` 均被拒。用途／圖型不相容、圖型專屬 row／step／node 等結構缺失、結構文字未進 `visible_text`，都在 prompt 寫出前被拒；對照圖另有左右欄及逐列配對反例。主要作品漏標及比較作品誤升格都有反例。每頁主標有可讀回來源並在 prompt／可見文字白名單／圖片讀回內；標題、來源、作者、日期、數字、評分、頁碼與 footer 必須逐字核對；一般正文卡片允許在不改變意思、不新增主張、不反轉限制下因排版壓縮，並需有語義一致讀回。正式檔名由 compiler 產生；prompt 有第三方自足內容契約；action 鎖定 Codex imagegen / `gpt-image-2`、目標畫布、品質方向與 expected asset；單次派發帳本禁止一般重生與第三次生成；讀回綁定真實 PNG 與 prompt hash。契約升版只可遷移從未派發的頁面並封存舊 action；已有 PNG 只可經綁定現行核心讀回保留，不能以舊 repair 或審美提示重生。編譯後漂移、缺 attempt ledger、一般審美重試、無嚴重內容錯誤／遺漏證據的 repair、已通過頁重生及第三次生成都在 writer 前被拒 |
| 寫入與讀回 | 只經 `ObsidianVaultAdapter`；source-local readback、同一 source ID、registry 去重、增量 outcome URL 一致 |
| 恢復 | 模擬 capture、KP checker、image receipt、writer 任一中斷後，以同一 job/source identity 恢復且不重複寫入 |
| 執行與跨 runtime | 同一工作保持可恢復；執行器遵守 Input Bundle／KP／receipt，不把 session ID 當內容真源。背景派發須另有真實 runtime 能力證據，不能由本套件文件推定 |
| 時間證據 | 量度整段 wall time，並對 URL、文字、圖片各完成一份真實 UAT；超時原因可讀回，不能以 fixture 或 checkpoint 重跑冒充達標。可重跑基準：`tools/ingest_benchmark.py prepare --out <空白暫存目錄>` 以現行工具、空白 Vault 及固定四類真實輸入建立隔離工作區，由 AI 只按使用者平日一句入口指令（`$brain-url`／`$brain-text`／`$brain-image`）裸跑，再以 `report --root <目錄>` 匯總各 receipt；有額外操作指示的執行不算入口驗收 |

## 9. 可攜部署與停止條件

部署只建立乾淨工作區、工具、規格和空白 Vault，不匯入私人筆記或歷史收據。具體安裝命令由套件入口提供。部署後所有命令以新工作區為 cwd，root-relative 路徑不得解析到工作區外。

離線 fixture 必須標示，不能成為正式來源、圖片或完成憑證。真實 URL、文字、圖片的全程驗收各有自身 source identity；URL 擷取、新生圖及外部能力只在使用當時確認。未有 live 證據時不得聲稱新電腦已通過三入口真實 UAT 或十分鐘 SLA。

`project --write-vault` 已完成後重跑須零寫入讀回；KP、投影、帳本、憑證或成果雜湊漂移即阻擋。未實作的背景能力不在本套件可用交付範圍。
