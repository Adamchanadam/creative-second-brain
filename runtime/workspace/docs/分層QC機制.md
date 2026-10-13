# 分層 QC 機制

本文件只路由情景的真源、最小檢查及停止點；領域規則由對應文件擁有。manifest是路由投影，不是代跑測試或完成證據。命令以部署後workspace為cwd，參數中的相對路徑由實際工作回傳，不猜造。

## 1. 使用原則

普通入庫只驗本篇；已讀且未改的規格可沿用，圖像全文義務仍有效。修共用工具/規格、整包就緒聲稱或資料/權限/外部邊界變更時，追加相鄰反例及獨立讀回。fixture保持隔離，不建立第二writer或完成帳本。

## 2. 情景路由表

| 情景 | CLI 識別鍵 | 先讀真源 | 必跑檢查 | 失敗時停在哪裡 |
|---|---|---|---|---|
| 單一URL、文字或圖片入庫 | url-ingest | `docs/Compiler Worker與Knowledge Package設計及驗收規格.md`; `docs/URL到輸出模板規格.md`; `docs/資訊圖生成規格.md` | `python -B tools/url_ingest_qc_router.py --source-kind url --profile article --json`；按真實來源改source-kind/profile，讀正式工作單，本篇來源完整性/KP/PNG/唯一writer/outcome/receipt/candidate讀回 | source_blocked、partial_capture、needs_review、identity/hash不符或未完成圖片 |
| Compiler/rail契約修改 | compiler-worker-v2 | `docs/Compiler Worker與Knowledge Package設計及驗收規格.md`; `tools/compiler_worker_v2.py`; `tools/ingest_plan_contract.py` | `python -B tools/compiler_worker_v2.py capabilities`；隔離fixture驗三類輸入、來源完整性、圖片帳本、projector、writer/readback、完成/重播/恢復；只跑受影響反例 | 程式能力缺失、身份/雜湊漂移、缺帳本；離線fixture不得冒充真實UAT |
| 資訊圖生成或修訂 | infographic | `docs/資訊圖生成規格.md`; `docs/Compiler Worker與Knowledge Package設計及驗收規格.md` | 讀圖像全文及hash/字元receipt；按owner跑覆蓋/鎖定/真圖生成/逐項反審/PNG讀回；完成後修訂保留舊receipt | 缺能力或授權、內容/風格/追溯阻塞、無真實PNG/ledger |
| Vault寫入或文字附PNG | obsidian-write | `docs/URL到輸出模板規格.md`; `tools/obsidian_vault_adapter.py` | 本篇writer preflight與寫入讀回、原bytes與相對路徑、registry身份、附件原圖hash、完成報告 | needs_review、blocked、附件不符或正式讀回缺失 |
| 共用writer/registry/canonical架構 | vault-maintenance | `docs/URL到輸出模板規格.md`; `docs/多層驗收機制.md`; `tools/obsidian_vault_adapter.py` | `python -B tools/obsidian_vault_adapter.py --vault-health --json`；隔離測試失敗/並行/身份/恢復，不默認壓力測試 | 新缺陷、未裁決身份矛盾或可能覆寫資料 |
| 知識取材及問答 | knowledge-retrieval | `docs/Obsidian語義索引與關聯圖落地方案.md`; `tools/ask_second_brain.py` | `python -B tools/knowledge_material_selector.py --query "問題" --json`；用prepare packet實讀evidence，answer核對引用hash，AI再驗語義 | context_ready不等於回答；缺證據/過期packet/不支援主張 |
| 創造型圖網及整理 | obsidian-semantic-graph | `docs/Obsidian語義索引與關聯圖落地方案.md`; `tools/obsidian_semantic_maintenance.py` | `python -B tools/obsidian_semantic_map_flow.py --check --quality-limit 9999 --json`；按任務讀回整理/本篇候選/自選五題receipt | not_configured、insufficient_evidence、needs_review；候選不能升格 |
| 成果頁與讀者文字 | reader-layer | `docs/成果頁索引資料層設計.md`; `docs/多層驗收機制.md`; `tools/outcome_page_quality_audit.py` | `python -B tools/outcome_page_quality_audit.py --source-id <來源ID> --json`；實際入口、圖片、正文、歸屬及手機/桌面閱讀檢查 | 重複/斷橋/內部流程語/假完成；未開實頁標未驗 |
| 成果頁生成與入口 | outcome-page | `docs/成果頁索引資料層設計.md`; `tools/outcome_page_generator.py` | 實際Vault生成後讀本篇source/outcome/index/Obsidian入口；不可只看HTML存在 | needs_review或入口/PNG缺失 |
| 可重建索引dry-run | outcome-index | `docs/成果頁索引資料層設計.md`; `tools/outcome_index_builder.py` | `python -B tools/outcome_index_builder.py --dry-run --json`；row/缺欄位/facets/頁sample | 停在dry-run；不稱SQLite/FTS/萬筆能力 |
| 完成核對與接續 | completion-review | `docs/Compiler Worker與Knowledge Package設計及驗收規格.md`; `docs/URL到輸出模板規格.md` | `python -B tools/ingest_rail.py inspect --job-id <工作ID>`；唯讀核對原工作/receipt/產物，接續才按合法next action | 歷史完成未核實、派發不明、hash漂移、原工作缺失 |
| 手冊更新 | manual-guide | `docs/成果頁索引資料層設計.md`; `docs/README.md` | 核對owner變更、所有連結/用途路由、搜尋/複製fallback/展開/列印、桌面/手機；來源指紋只作提醒 | 斷鏈、過期功能或未驗的可用聲稱 |
| 可攜套件整體驗收 | portable-package | `docs/README.md`; `docs/多層驗收機制.md`; `docs/分層QC機制.md` | 乾淨隔離workspace、依賴/相對路徑/零私人資料/原檔保護、各已交付功能反例、獨立審閱；新機live能力另外probe | 漏依賴、越界、私人內容、未裁決反例或把fixture當live |

## 3. 停止與升級

source/identity/圖像/讀者品質有阻塞就停該來源的正式完成，保留可恢復工作。無關歷史或其他歷史成果警告不能阻擋已完成本篇。Notion寫入、背景探索、SQLite server及公開發布不屬本版可用功能，不用其他情景的ready升格。

## 4. 維護此路由

先修改規則owner，再更新本表的來源/檢查/停止點。表頭、情景CLI key與五欄格式供 `validation_manifest.py` 解析；不能以空表或固定ready繞過。只引用本包實際存在的docs/tools，不能依賴原工作區dev、私人路徑或未打包tests。
