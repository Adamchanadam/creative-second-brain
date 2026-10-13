# URL 到輸出模板規格

> 目的:定義一條已驗證來源的正式成果包與唯一 vault writer 邊界。來源可以是公開可讀 URL、使用者明確提供的可見文字，或使用者明確提供的單張圖片；三者不得互相冒充。工作身份及正式完成見 `docs/Compiler Worker與Knowledge Package設計及驗收規格.md`；資訊圖內容、生成及視覺驗收只見 `docs/資訊圖生成規格.md`。

## 1. 適用範圍

本文件保留正式成果包與唯一 vault writer 邊界；它不是 Compiler Worker 入口。新的 URL／使用者文字／使用者圖片入庫架構只由 `docs/Compiler Worker與Knowledge Package設計及驗收規格.md` 定義。相容 pipeline 只負責文字附既有 PNG 的直接保留分支及既有工作修復；不得承擔新架構設計或冒充 v2 完成。

本模板適用於第一階段的單一來源入庫流程。允許的正式來源輸入只有：

1. `url`：公開可讀 URL，由擷取工具取得正文；或
2. `user_provided_text`：使用者明確指定的 UTF-8 文字 / Markdown 正文，由原始 bytes 的 SHA-256 身分驗證；或
3. `user_provided_image`：使用者明確指定的一張 PNG、JPEG 或 WebP 原圖，由原始 bytes 的 SHA-256 身分驗證。

新來源的固定工作單入口見 Compiler Worker 規格 7.2；URL／貼文一律保留全文，圖片保留原圖及經核對的完整可辨文字。

三種輸入都必須經相同的來源分析、資訊圖與正式 writer 閘門；`user_provided_text` 和 `user_provided_image` 不得填入、推測或以其他 URL 代替其來源身分。

`user_provided_text`／`user_provided_image`只表示內容由誰交到系統，不表示該人是作者、發布者或內容歸屬者。若文字標示「非 <姓名> 內容」，這只是不可以把內容歸到該人名下；作者、來源機構與內容歸屬必須依正文或明示來源資料填寫。原始出處未提供時，保留正文已明示的人物／機構角色，並把原作者或發布者標為未提供／待核實，不可按提交者身分推定。

`user_provided_text` 可以同時附帶多張使用者既有 PNG；它們仍屬同一份文字來源的資訊圖資產，不會轉成另一種來源輸入或被重新生成。正式 writer 必須把任何 package-local Markdown 圖片連結重寫為 vault 內的 Obsidian embed，並逐張驗證原圖為不透明 PNG、讀者可見內容與保留的原有 footer。其他來源、覆蓋、學習、應用與讀回門檻維持不變。

流程包括:

1. 來源可讀性稽核。
2. Markdown 真源知識檔。
3. 本機 assets 目錄。
4. 原文助讀 PNG 資訊圖。
5. Obsidian-ready 來源索引欄位。
6. 原子知識點草案與關係草案。
7. application note 內的個人化扣連、跨來源洞見判斷與採納判斷。
8. idea layer 候選：可重用 concept、hub、tension、taste signal、application_scene 與 semantic bridge 候選。

不適用於:

1. 付費牆、登入牆、只讀到摘要或正文不完整的 URL。
2. 批量入庫。
3. 已正式寫入 Obsidian vault 的關聯試驗。
4. 第二階段品味探索網自動化。

正式 graph 投影與 bridge 整理只看 `docs/Obsidian語義索引與關聯圖落地方案.md`。本模板只負責在單一來源入庫時保存足夠候選材料，不把候選關係直接升格為已核實 graph link。

## 2. 可攜樣本邊界

本套件只使用獨立標示的合成測試資料驗證機制，不附帶任何人的正式知識、私人物件或歷史完成收據。測試通過不會把 fixture 升為正式來源。

## 3. 輸出包目錄

每條有效來源使用一個獨立輸出包:

```text
outputs/vertical_slice_YYYY-MM-DD_<slug>/
  KS-VS-<序號>_<slug>.md
  assets/
    KS-VS-<序號>_<slug>_source.png
    <其他候選或原圖,只在需要保留證據時保存>
```

### Windows 路徑安全邊界

本節是所有來源成果包（相容 pipeline 與 Compiler Worker v2）的唯一檔名／路徑長度規則。`slug` 不可由 AI 自由保留為無上限名稱：最多 40 個字元；超出時保留前綴，再加上 `_` 及原 slug 的 SHA-256 前 8 碼，以維持可讀性與可追查性。`source_id` 最多 20 個字元，超出時必須在建立任何檔案前拒絕，不可靜默改寫身份。

每次建立成果包、資產或原圖前，工具必須驗證實際完整路徑不超過 256 個字元；原子 JSON 寫入還必須驗證實際暫存檔名同樣不超限。若工作根目錄本身太深，工具以 `blocked` 停止，不能寫出半個成果包。原文標題與內容不截短，只有用於檔名的 slug 受此規則限制。

README 不是正式產品產物。只有在需要保存 dry-run 驗收、候選圖排除、外部試寫讀回等測試封套資訊時才建立。

## 4. Markdown 真源固定結構

每份 `KS-VS-*.md` 必須按以下順序組成。

| 區段 | 必須內容 | 邊界 |
|---|---|---|
| 來源稽核 | `來源輸入類型`、來源名稱、類型、作者 / 發布日期如有、擷取日期、抓取方式、可讀性、正文保存模式、完整度、是否可入庫、vault write status、AI 來源分析狀態、AI 來源分析證據；`url` 必填 URL，`user_provided_text` 必填 `來源身分` 和 `來源內容 SHA-256` | URL 未達 `public_ok`、使用者文字未達 `user_text_complete`，或 AI 來源分析未完成都不得生成正式成果 |
| 視覺解讀與校對 | 只適用於 `user_provided_image`；原圖相對路徑、原圖 SHA-256、MIME、尺寸、逐字轉寫、正規化 `IMG-*` 區域錨點、圖片可見主張／外部核對狀態、每個錨點的覆蓋去向及第二輪校對證據 | 原圖未保留、分析未完成、覆蓋表漏錨點、第二輪未通過或知識點不能回指 `IMG-*` 都不得正式寫入 |
| 正文保存區 | 只在 `正文保存模式=full_body_required` 時必填；包含保存模式、canonical article body SHA-256、正文清理邊界、代表錨點，以及 `### Canonical article body` 下的清理後正文主體 | 必須由正式 prepare 入口帶入 review packet 與 source slice；不得只靠 AI 事後手動補欄位；排除網站導覽、留言、彈窗、上一篇/下一篇、搜尋、商品提示等非正文內容 |
| 原文層 | 原文主張、原文例子 / 人物 / 數據、原文證據 | 不補寫原文沒有的立場 |
| 一句定位 | 一句概括原文主旨 | 只可概括原文 |
| 來源知識摘要 | 3 至 5 點快速摘要 | 這是完整來源成果包的摘要；不把個人應用寫成原文摘要 |
| 關鍵知識點 | 原文主張、方法、數據、限制、治理、轉化層分開列；v2 另以 `coverage_units` 保留支援單元的處置 | 每點要能回到原文或明確標示轉化層；高價值支援單元不可被摘要壓縮後靜默刪走 |
| 新手入口 | 讀懂本文前需要的概念 | 無必要前置就寫明 |
| 概念拆解 | 日常類比、正式定義、適用邊界 | 不用術語堆砌代替理解 |
| 知識用途分流 | 主要用途、是否入事實知識網、是否入品味探索網、不入或入網原因、即時實用產出、第二階段訊號 | 事實網與品味網不可混成同一判斷 |
| 個人應用層 | slice 使用 `## 個人化消化`；writer 只投影到 `applications/APP-*.md`，包含個人化扣連、跨來源洞見判斷、採納判斷與可行動建議，不留在 source note | source note 只消化來源正文內容；AI 執行狀態只寫同一工作帳本、package 或檢查收據 |
| Idea layer 候選 | 不寫入 source note;如有可重用想法,另在 application note 或 KP 中保存 concept、hub、tension、taste signal、application_scene 與 semantic bridge 候選 | 候選不是已核實 graph link;record ID 只作 plain text 查證,不可用 `KP-...` / `APP-...` wikilink 進主 graph；後續提取驗收只看 `docs/Obsidian語義索引與關聯圖落地方案.md`、`tools/obsidian_idea_retrieval.py` 與 `tools/obsidian_semantic_maintenance.py` |
| 資訊圖 | 正式 PNG 的相對路徑、檔案雜湊及 source note embed | 資產必須已按 `docs/資訊圖生成規格.md` 完成生成及驗收；本文不重述其方法或門檻 |
| 原子知識點與關係 | 知識點 ID、內容、層級、可信度、來源證據、關係表 | Obsidian 寫入前仍是草案 |
| 學習檢查 | 理解題、保持題、遷移題、應用任務；可按題材另加辨析或風險題 | 必附答案標準或評分依據；應用任務必須是可執行小任務，不可只寫成抽象應用題 |
| Obsidian-ready 索引欄位 | source note properties、knowledge-point note properties、Bases 視圖欄位、已寫入時的讀回紀錄 | Obsidian source note 是產品主資料層;Bases 只作視圖,不取代 Markdown 真源 |

2nd Brain source note 的閱讀目標是「準確理解來源正文講甚麼」。個人應用、AI 下一步、仍需驗證與 adapter 任務狀態不得放入 `sources/*.md` 正文。個人應用由 slice 的 `## 個人化消化` 投影到 `applications/APP-*.md`；讀者頁標題為「與你有何關係」。AI 執行狀態只保存在同一工作帳本、package 或檢查收據，不混入來源正文。個人應用的相關程度使用通用欄位 `personal_relevance`。

### 4.1 深入解讀與可重建閱讀頁

使用者要求深入解讀時，製作前先把要求對應到正式資料欄位；完整解讀保存於 `application.analysis_markdown`，由既有 slice → 唯一 writer → application note → 共用 reader 投影。不得以獨立 HTML、外鏈摘要或針對來源編號硬碼的生成器代替應入庫正文。主要官方全文仍屬 canonical source；補充來源以 `supporting_sources` 分別保存 URL、身分、支持章節與正文快照雜湊。

圖片計劃已鎖定、正式 writer 尚未開始時，透過 `brain.py rail amend-application --job-id <原工作> --response-file <補充請求>` 保存完整應用及必要的 `analysis` 修訂；必須綁原 scope、通過既有 schema／內容核對，保留來源、圖片、修復次數與開始時間。中斷的補充交易必須先以同一請求恢復，不能越過寫入。writer 已開始後不可用此入口改內容。

`related_sources` 只表達有說明的閱讀關係，不冒充同一來源或知識圖已核實關係。要求雙向入口時，完成新篇後使用既有 `refresh-outcome` 重建關係兩端，並實際讀回目錄、完整應用正文及兩端可點擊入口；原篇正文及圖片不得因投影更新而改寫。未完成這些讀回，不得只憑 HTML 存在聲稱完成。

## 5. 來源稽核規則

`可讀性狀態` 只使用以下值:

| 狀態 | 意義 | 可否生成正式 Markdown / PNG / Obsidian-ready 欄位 |
|---|---|---|
| `public_ok` | 正文主體可公開讀取 | 可以 |
| `user_text_pending_review` | 使用者提供正文已保存，但尚未完成逐字來源分析與審閱 | 不可以 |
| `user_text_complete` | 使用者提供正文已完成來源分析；`source_capture.md`、審核 slice 與 metadata 的內容 SHA-256 完全一致 | 可以 |
| `user_image_pending_review` | 使用者原圖已保存，但視覺解讀、覆蓋表或第二輪校對未完成 | 不可以 |
| `user_image_complete` | 使用者原圖、metadata、review packet、視覺解讀及 slice 的 SHA-256／尺寸完全一致，且每個視覺錨點已覆蓋和第二輪校對通過 | 可以 |
| `partial_capture` | 只讀到部分正文或不確定完整 | 不可以,只保存阻塞紀錄 |
| `source_blocked` | 登入牆、訂閱牆、地區阻擋、腳本遮罩或無正文 | 不可以 |
| `invalidated` | 事後發現來源或輸出不合格 | 不可以,只作失敗證據 |

來源主張、來源數字與來源限制必須標明是「原文指出」或等效表述。媒體文章涉及政策、法規、撥款或官方限制時,只能作媒體來源主張;未回官方文件前不得升格為官方事實。

AI 必須完成來源分析,不可把 source analysis 留給使用者作一級驗收。正式入庫前,來源稽核表必須包含:

| 欄位 | 規則 |
|---|---|
| AI 來源分析狀態 | `pending` / `complete`;正式寫入只接受 `complete` |
| AI 來源分析證據 | 一句說明 AI 已按 capture 補完哪些來源層欄位,例如來源稽核、原文主張、原文證據、TL;DR、知識點與關係 |

使用者只負責採納、價值取捨、個人口味與後續行動判斷;不負責替 AI 完成原文分析或 PNG 語義驗收。

對 `user_provided_text`，正式審核包必須保存 `source_capture.md` 的原始 bytes、`source_capture_metadata.json` 的 `source_input_kind`、`source_identity` 和 `source_content_sha256`。原始輸入所在的本機絕對路徑不得寫入輸出包、vault、registry、成果頁或日誌。任何 hash 不一致、metadata 不一致、缺少審核包或來源類型混淆，都必須在寫入前以 `blocked` / `needs_review` 停止。

### 5.1 正文保存模式

正式入口必須明確帶入正文保存模式，並寫入 `source_capture_metadata.json`、`review_packet.json` 與 source slice 來源稽核表。

| 模式 | 使用時機 | writer 閘門 |
|---|---|---|
| `summarized_allowed` | 已存在的相容／歷史入庫包 | 只供歷史讀回或原包修復；新文字與 URL 接單不得選用 |
| `full_body_required` | 新文字與 URL 入庫，包含 v2 及相容入口的新接單 | `source slice` 與 Obsidian source note 必須包含清理後 canonical article body；metadata、review packet、source note 與 registry 的 `canonical_body_sha256` 必須一致；代表錨點必須能從正文區讀回 |
| `original_image_required` | 使用者明確提供一張圖片作知識來源 | source slice、metadata、review packet、registry 與 source note 必須共用同一原圖 SHA-256；vault 必須保存原圖，並可讀回視覺解讀、`IMG-*` 錨點和第二輪校對 |

`full_body_required` 不可只由 AI 審稿時自行填寫。正式 prepare 入口必須把此意圖、`canonical_body_sha256`、`canonical_body_representative_anchors` 寫入審核包；若 source slice 缺正文保存區、hash 不一致、代表錨點不存在，或 completion report 不能讀回正文區，正式寫入必須停止。若只讀到網站摘要或片段，狀態仍是 `partial_capture` / `source_blocked`，不得以 full-body 模式宣稱完成。

`original_image_required` 同樣不可由 AI 事後補欄位。正式 prepare 入口必須保存原圖 bytes 和 SHA-256，image-review 必須以原圖的 SHA-256、尺寸及 `IMG-*` 錨點驗證結構、逐字轉寫、來源主張、覆蓋表和第二輪校對。OCR 只屬轉寫候選；圖片可見內容、外部已核實事實及仍需外部核對的主張必須分開。不得把原圖當成由 imagegen 產生的資訊圖，也不得把摘要取代原圖。

## 6. 資訊圖成果引用

URL 成果包只負責保存正式 PNG 的 vault 相對路徑、檔案雜湊及 source note embed；資訊圖鎖定稿、覆蓋表、頁數、候選上限、圖示、母版、生成授權與驗收全部由 `docs/資訊圖生成規格.md` 唯一定義。本文不得複製或改寫那些規則。

## 7. Obsidian-ready 來源欄位

正式產品流程應由 ObsidianVaultAdapter 負責 vault 寫入、YAML properties 驗證、來源身分查重、registry 登記、internal links、附件 embed、Bases 視圖與 read-back 驗證。Notion 只保留為已完成的 R&D 技術流程實驗,不得成為正式產品主線。

source note properties 草案至少包含:

| 欄位 | 規則 |
|---|---|
| 標題 | 來源名稱,不是內部任務名 |
| source_input_kind | `url`、`user_provided_text` 或 `user_provided_image`；不可省略於新寫入來源 |
| source_identity | 新來源的唯一去重鍵；URL 為 `url:<標準化 URL hash>`，使用者文字為 `user_provided_text:<原始 bytes SHA-256>`，使用者圖片為 `user_provided_image:<原始 bytes SHA-256>` |
| source_retention_mode | `summarized_allowed`、`full_body_required` 或 `original_image_required`；full-body / original-image 模式必須由正式入口帶入並由 writer / completion readback 驗證 |
| canonical_body_sha256 | 只適用於 `full_body_required`；必須等於 source note `## 正文保存區` 中清理後正文主體的 SHA-256 |
| 來源URL / source_url_hash | 只適用於 `url`；分別為原始 URL 與標準化 URL hash |
| source_content_sha256 | 只適用於 `user_provided_text`；必須等於審核包 `source_capture.md` 的原始 bytes SHA-256 |
| source_image_sha256 / source_original_image | 只適用於 `user_provided_image`；必須等於保存原圖的 raw bytes SHA-256，並指向 vault 內可讀回的原圖資產 |
| source_image_mime / source_image_width / source_image_height | 只適用於 `user_provided_image`；必須與原圖的實際 MIME 和尺寸一致 |
| 來源類型 | 使用 Obsidian source note schema;若不匹配,先用最接近類型並在 Markdown 寫清楚 |
| content_owner | 內容歸屬；只在正文或明示來源資料足以指出時填寫，可記錄正文中明示的分享者、受訪者或作品／品牌歸屬，但不得把提交者當成內容 owner |
| 作者 / 發布日期 | 原文或可核實來源有才填；作者不可與受訪者、分享者或提交者混為一談 |
| 知識用途 | 以用途分流結果填寫 |
| 摘要 | 只描述來源正文內容;properties 內只放短摘要,長摘要寫入 note body |
| registry_path | vault 相對路徑,固定為 `_registry/sources.jsonl` |
| source_note_path | vault 相對路徑,格式如 `sources/YYYY/MM/<source_id>_<slug>.md` |
| source_archive_path | 如需保存過長原文或原始抓取,用 `source-archives/YYYY/MM/...`;不需要時留空 |
| assets_folder | vault 相對路徑,例如 `assets/ks-vs-0001/` |
| infographics | vault 相對路徑清單,由 note body 用 `![[...]]` embed |
| preview_status | `embedded` / `missing_asset` / `not_available` |
| preview_note | 只寫預覽狀態證據,不寫內部待辦長文 |
| 真源狀態 | `draft_public_ok` / `public_ok` / `invalidated` / `partial_capture` / `source_blocked` |
| 來源可信度 | 高 / 中 / 低,並說明是否仍需官方文件核對 |
| 品味入網狀態 | 對齊來源是否進入品味探索網 |
| vault_write_status | `not_written` / `written` / `read_back_verified` / `write_failed` |
| knowledge_points | knowledge-point note links,正式路徑應位於 `knowledge-points/by-source/<source_id>/` |
| application note `adoption_status` | 收藏 / 再訪 / 試行 / 深讀 / 暫存 / 拒絕;正式寫入時由 AI 判斷並直接生效 |
| application note `adoption_reason` | AI 採納判斷的一句原因;正式寫入時同步採用 AI 理由 |
| application note `ai_adoption_status` | AI 採納判斷欄;UI 顯示為 AI 建議,正式寫入後直接生效 |
| application note `ai_adoption_reason` | AI 判斷的一句原因；讀者應可在 30 秒快讀中找到採納狀態與理由 |
| application note `ai_review_confidence` | 高 / 中 / 低;用來提示 AI 判斷是否足夠可靠 |
| application note `manual_correction_status` | 無修正 / 需修正 / 已修正;只有不同意 AI 判斷時才由人手改動 |
| application note `manual_correction_action` | 不同意 AI 判斷時才改 status；由使用者明確要求的 APP 修正保存，不假設已交付互動審閱台 |
| application note `personalization_status` | `linked` / `personalization_blocked`;已讀到可扣連材料時寫 `linked`,讀不到既有知識網時寫 `personalization_blocked` |
| application note `personalization_links` | 新來源扣連到既有知識、項目、問題或規格的短句清單;詳細理由寫 APP note body |
| application note `compounding_insight_status` | `has_insight` / `no_compounding_insight` / `needs_context`;表示新內容是否對既有知識網產生 ⚡ 複利洞見 |
| application note `compounding_insight_types` | 補底 / 加深 / 連接 / 修正 / 行動;可多選,五類都不成立時留空並用 `no_compounding_insight` 說明 |
| application note `compounding_insight_links` | 新 URL 扣連到哪些既有 source / knowledge point / application / project anchor;詳細理由寫 APP note body |
| application note `compounding_next_action` | `has_insight` 與 `needs_context` 必須提供具體行動；缺脈絡時寫要取得的資料或下一輪查核。只有 `no_compounding_insight` 可填「暫無」。 |
| application note `taste_link_status` | `linked` / `taste_needs_context` / `exploration_needs_context` / `not_applicable`;不可把非品味材料硬湊成品味洞見 |
| application note `taste_links` | 品味或生活扣連短句清單;非品味材料留空並用 `not_applicable` 說明 |

application note body 必須包含「跨來源洞見判斷」小節,對外語義為「⚡ 複利洞見」。此小節逐項回答補底、加深、連接、修正、行動五類。每一類要寫成立 / 不成立 / 需要更多脈絡,並列出證據錨點。不得把同詞相似、主題相近或普通整理寫成洞見。

「跨來源洞見判斷」同時是 semantic bridge 的候選原料，但不是正式 bridge。若新來源產生可重用 idea，application note 應以小節或列表保存以下內容：

| 候選 | 要回答 | 例子 |
|---|---|---|
| `concept` | 這篇來源提供甚麼可重用想法 | `工具擴展與失敗處理` |
| `hub` | 它應該進入哪個主題場 | `工具使用邊界` |
| `tension` | 它形成甚麼取捨或矛盾 | `效率 vs 品味` |
| `taste_signal` | 它暴露甚麼反覆吸引、排斥、信任或懷疑 | `偏好可驗證的完成證據` |
| `application_scene` | 它可以用在哪個工作、學習、創作或生活場景 | `如何判斷一篇文章值得入庫` |
| `semantic_bridge` | 它支持、反駁、延伸、限制或可應用於哪個既有 idea | `支持：AI 採用成效衡量` |

候選 bridge 必須標明關係類型：支持、反例、延伸、限制、形成取捨、可應用於、品味線索或需要查證。若只因同詞相似、分類相近或 AI 猜測而來，只可標為 `需要查證`，不得建立正式 concept-to-concept wikilink。

寫入 Obsidian 前必須先由 adapter 以 `source_identity` 查 `_registry/sources.jsonl` 去重。舊 URL registry row 可由既有 `source_url_hash` 推導 `url:<hash>` 以保持相容。已存在則讀回原 source note 驗證來源身分與 `source_id`，再更新原 note，不得新增重複來源。registry 與 note 不一致時，狀態必須改為 `needs_review`，不得靜默合併或另建一份。

審計紀錄只作證據,不得承擔正文真源、素材目錄、資訊圖路徑或狀態欄位。

## 8. 知識點草案

每份 Markdown 真源先保存 3 至 8 條主原子知識點草案。較長或多方法來源必須另以 v2 `coverage_units` / omission receipt 保留支援單元、處置與省略理由；不可因主 KP 數量上限靜默刪掉高價值內容。每條知識點至少包含:

| 欄位 | 規則 |
|---|---|
| ID | 本機暫用 `KP-<來源縮寫>-<序號>` |
| 知識點 | 一句可獨立成立的主張 |
| 層級 | 原文主張 / 原文方法 / 原文數據 / 原文限制 / 原文治理 / AI 轉化 |
| 可信度 | 高 / 中 / 低 |
| 來源證據 | 原文中足以支持整句主張的完整逐字句子或段落摘錄；不可只寫小節名、關鍵字或片語。寫入前 adapter 必須核對摘錄確實出現在 canonical 原文。 |

關係表至少包含來源知識點、目標知識點、關係、證據錨點、信心。證據錨點同樣必須是能支持該關係的完整原文摘錄。source note 內的關係只可承載來源正文內容與忠實轉化;若目標是本專案需求、使用者既有知識或個人應用,必須另寫 `applications/APP-*.md`,不可冒充來源正文。

source-local 關係表只保存同一來源內的 KP 關係，不等於 2nd Brain semantic bridge。跨來源、品味、hub、tension 或應用場景關係必須先作 idea 候選，經 `docs/Obsidian語義索引與關聯圖落地方案.md` 的 bridge 規則整理後才可進主 graph。

ObsidianVaultAdapter 寫入 knowledge-point note 時,必須同步維護 `_registry/knowledge_points.jsonl` 與 `_registry/relations.jsonl`:

1. 每條 knowledge point 以 `kp_uid = <source_id>:<kp_id>` 作唯一 row key。
2. `semantic_key` 由知識點文字正規化後 hash 生成,用於查出疑似重複;同 key 只標成 `duplicate_candidate`,不得自動合併。
3. relation registry 只登記兩端都已寫入 knowledge-point note 的 source 內關係。
4. 指向本專案、個人應用、個人脈絡或未寫入節點的關係,不進 source 關係 registry。
5. registry 寫入需有鎖,避免並行程序覆寫彼此 row。

## 9. 入庫前驗收

正式標記為有效切片前,逐項檢查:

1. `url` 來源狀態是 `public_ok`；`user_provided_text` 是 `user_text_complete`，且審核包 bytes、metadata 和 slice 的內容 SHA-256 一致；`user_provided_image` 是 `user_image_complete`，且原圖 bytes、metadata、review packet、visual analysis、slice 和 vault 原圖的 SHA-256 一致。
2. Markdown 真源有來源稽核、原文層、轉化層,且不混入 個人應用層或 AI 內部行動。
3. TL;DR 與來源摘要沒有加入 個人應用或本專案內部流程。
4. 資訊圖成果已通過 `docs/資訊圖生成規格.md` 的完整驗收。
5. PNG 路徑、檔案雜湊及 source note embed 可讀回。
7. Obsidian-ready 欄位含 source identity、registry path、source note path、assets folder、infographics、真源狀態、preview status、vault write status；只有 URL 來源另含 source URL hash。
8. 不生成或嵌入 Excalidraw map;第一階段只保留 source note、PNG 資訊圖、knowledge-point notes、application note 與 Bases。
9. 原子知識點有來源證據,關係不是泛稱「相關」。
10. application note 已完成 ⚡ 複利洞見判斷;五類都不成立時,必須標記 `no_compounding_insight` 或「暫無複利洞見」,不可硬湊。
11. application note 已按內容價值判斷是否有 idea layer 候選；沒有候選時要寫明「暫無可重用 idea 候選」或等效狀態，不可把所有來源硬湊成 hub / tension / taste signal。單一來源完成不要求即時更新正式 graph；新增內容候選由 `tools/obsidian_semantic_maintenance.py --new-content-candidates --write-receipts --check --json` 另行讀回，正式提取路徑與五問 receipt 由 Obsidian 語義索引方案另行驗收。
12. 若未寫 Obsidian vault,狀態明確是 `not_written`;若已寫,必須有 source registry、knowledge-point registry、relation registry 與 source note / knowledge-point note 讀回紀錄。
13. 若題材涉及政策、撥款、法規或官方限制,需標明哪些仍需官方文件核對。
14. 若輸出事後不合格,改為 `invalidated`,並只作失敗證據。

## 10. 禁止事項

1. 不得由 URL 標題、搜尋摘要或記憶補寫正文。
2. 不得把 AI 推論、個人應用或 AI 內部行動寫成原文主張。
3. 不得把 Bases 視圖或任何外部 row 當正文真源。
4. 不得把資訊圖當 AI 抽取資料的主來源。
5. 不得接收未通過 `docs/資訊圖生成規格.md` 驗收的資訊圖資產。
6. 不得在本 repo 建 OAuth callback 或 redirect URI 流程。
7. 不得批量寫入 vault,除非另有明確批准。
8. 不得把失敗切片恢復為模板樣本。
9. 不得把 Notion API adapter 當正式產品主線;正式資料寫入應收斂到 ObsidianVaultAdapter。
10. 第一階段不得生成或嵌入 Excalidraw map;它會要求讀者理解非內容架構,目前不列入 URL 消化輸出。
11. 不得用全庫線性掃描作正式去重主路徑;正式寫入必須先查 `_registry/sources.jsonl`。
12. 不得以虛構 URL、檔案 URI、目錄名、舊附件內容或舊內容雜湊替代 `user_provided_text` 或 `user_provided_image` 的正式來源身分；不得持久化使用者原始本機檔案絕對路徑。
13. 不得把 idea 候選直接包裝成已核實 graph link；未有證據時只可列為 `需要查證`。
14. 不得用 `KP-...`、`KS-...`、`APP-...` 這類 record ID 做主 graph wikilink；它們只能作查證來源文字。

## 11. Vault writer 穩定邊界

1. 每次只選一個已驗證來源 slice。新 v2 來源依 Compiler 規格；文字附既有 PNG 依本文件第 1 節及第 12 節。
2. 正式寫入只經 `tools/obsidian_vault_adapter.py`。以 `_registry/sources.jsonl` 和 `source_identity` 查重；舊 URL 的 `source_url_hash` 只作相容讀取。registry 與 note 身份矛盾時停止，不靜默合併。
3. canonical 路徑是 `sources/YYYY/MM/`、`applications/YYYY/MM/`、`knowledge-points/by-source/<source_id>/`、`assets/`；不將原始本機絕對路徑寫入產品。
4. 每條 KP 的來源證據可回到正式來源；主 graph 的機器 ID 連結限制仍依語義圖規格。
5. 寫入後只做本篇讀回：來源身份、registry、YAML、正文／原圖、KP、source-local relations、APP、附件 embed、資訊圖雜湊、outcome 與增量入口。全庫健康、歷史 duplicate scan 及 Bases 整理不作本篇完成條件。
6. AI 負責首次來源與採納判斷；採納值正式生效但明示其 AI 來源，人只在不同意時修正。新使用者沒有既有脈絡時保留 needs_context / personalization_blocked，不套入預置個人偏好。
7. 不作批量 ingestion。既有完成收據與圖片不可無痕覆寫；修訂依 Compiler 的 revision 契約。

## 12. 文字附現成 PNG 的保留分支

此分支不經不支援附件的 `ingest_rail.py start`，也不創造假 imagegen receipt。使用者已要求入庫並保留附件時，AI 先用 `firecrawl_obsidian_pipeline.py prepare --user-text-file <原文> --source-type "使用者提供文字" --source-retention-mode full_body_required --json` 建立同一待審包。

讀回完整 `source_capture.md`、metadata、review packet 及生成的 slice。把使用者指定的 PNG 原 bytes 保存到該包 assets，以相對 Markdown 圖片連結引用；逐張確認原圖、hash、不透明 PNG、可見文字和原有 footer。不得改圖、套上本套件簽名或重生。來源、摘要、知識點、學習題、應用、覆蓋與身份仍須通過本文件。

每張附件的 `PNG 生成方式` 用可執行標記 `使用者提供 PNG 原圖保留`。逐張實際讀圖後，分別填 `PNG 語義驗收狀態`、`PNG 語義驗收證據`、`PNG 反審狀態` 及 `PNG 反審證據`；狀態只有真實通過後才填 `passed`，不可預填。反審證據須包含圖像規格全文 `spec_full_sha256` / `spec_chars` receipt、`docs/資訊圖生成規格.md`、可見文字逐項或逐字核對、`Bauhaus 手繪知識卡片版式語言`、原圖 footer 保留及失敗反例檢查。附件保留不偽造 Codex provider/model，也不把原有 footer 改成通用簽名；其餘品質閘仍依圖像規格。若既有圖不符合來源或品質要求，保留原圖並明示阻塞，不擅改。

補完並核對同一 slice 後，先用 `firecrawl_obsidian_pipeline.py ingest --source-slice <slice> --vault <本工作區的 vault> --json` 作非寫入 preflight；通過後同一入口加 `--write --completion-report-out <包內報告.json>`。完成必須讀回正式 completion report、唯一 writer 的本篇結果、原圖雜湊、source/APP/KP/registry、成果頁及入口；再按語義圖規格產生本篇候選收據。這是相容 writer 的完成報告，不自稱 v2 immutable receipt。對使用者回報的耗時須從本次實際接單起算；相容報告本身沒有 v2 execution 計時，缺少開始時間證據時明示未量得，不能把 prepare 或 preflight 當完成，也不能借用其他工作計時。
