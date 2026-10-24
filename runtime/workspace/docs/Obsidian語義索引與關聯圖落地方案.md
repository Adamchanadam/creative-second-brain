# Obsidian 語義索引與關聯圖落地方案

本文件是可攜創造型 2nd Brain 的 idea layer、語義橋接、問答、相鄰探索及整理規則 owner。正式內容保留在使用者的 Vault，不以工具輸出、聊天記憶或 HTML 取代。

## 1. Record 保存證據，Idea 保存想法

| 層 | 載體 | 作用 |
|---|---|---|
| Record | sources、knowledge-points、applications、registry、原圖及資訊圖 | 查證來源、保存內容與採納判斷 |
| Idea | concepts 內的 concept、hub、tension、taste_signal、application_scene | 聯想、比較、取捨、判斷與重用 |
| 閱讀投影 | maps 入口、HTML 目錄及成果頁、Bases | 導覽、搜尋及閱讀；不建立另一份內容真源 |
| 操作證據 | 工作 package、outputs receipts | 證明取材、候選、寫入與恢復；不進主圖 |

主路徑是「問題 → hub／tension／taste_signal／application_scene → concept → record」。乾淨 graph、線更多或隱藏 ID 均不足以證明可用；必須能讀回有證據的思考路徑。

Vault 主圖預設查詢為 `path:concepts -[concept_status:superseded]`，排除已撤回的生成概念；原 `showOrphans`、`hideUnresolved`、顏色和力學設定不因此重設。PUBLIC 比 OPS 額外顯示尚未連線的概念，讓首份有效材料不會因暫無已核實關係而看似空白；色彩、節點與力學設定沿用 OPS，連線仍只來自材料內已有證據的關係。配置檔寫入不能代表正在開啟的 Obsidian 視窗已套用；需要時以實際畫面確認。`AGENTS.md`、docs、tools、outputs、handoff、日誌、快取及測試資料不進知識圖。

Record ID（`KS-`、`KP-`、`APP-`、`SRC-`、`REL-`）只作 plain text 查證身份，不以 ID wikilink 進主圖；必要的來源閱讀連結仍可保留。Idea 之間可以用 wikilink，但關係要有理由和證據。

## 2. Idea 類型與語義橋接

| idea_type | 責任 | 不可做 |
|---|---|---|
| concept | 一個可重用想法 | 把整句原文或文章標題直接當概念 |
| hub／MOC | 跨來源主題場，回答一個核心問題 | 只堆目錄 |
| tension | 保存取捨、衝突、適用條件 | 把矛盾消平為「相關」 |
| taste_signal | 有證據的反覆吸引、排斥、信任或懷疑 | 把一次閱讀或 AI 猜測寫成已確認偏好 |
| application_scene | 可使用知識的工作、學習、創作或生活場景 | 與 applications/APP-*.md 的個人應用 record 混名 |

可重用概念由同輪分析判斷，欄位及新／舊 schema 相容只見 [Compiler §7.2](Compiler%20Worker與Knowledge%20Package設計及驗收規格.md#72-三種來源的程式工作單)。唯一概念責任點 `obsidian_vault_adapter.py` 的 `reusable_concept_decision`／`build_reusable_concept_entries` 供首次寫入及日後 missing／rewrite 共用。只接受有理由的分析名稱，不截 KP claim 的逗號、數字或標題來猜；來源事例、預測及待判斷項不自動升格。明確可重用的單源概念先保持 concept，不靠名稱關鍵字推成 hub、品味或場景；那些角色另需實際證據。source-local KP 關係不機械轉成 concept 線。

Bridge 需由至少一份正式 source、application、回饋事件或人工整理判斷支撐。不能因同詞相似、同題材或分類相近而自動連線。Obsidian 圖不顯示 typed edge，關係類型必須寫在 note 的小節內：

| 小節 | 語義 |
|---|---|
| 支持 | 另一 idea 加強此判斷 |
| 反例 | 推翻、限制或防止過度套用 |
| 延伸 | 引出下一個問題或方法 |
| 限制 | 說明成立邊界 |
| 形成取捨 | 要選擇、平衡或排序 |
| 可應用於 | 轉成具體使用場景 |
| 品味線索 | 有證據的偏好或拒絕 |
| 需要查證 | 候選，尚不足以成立正式橋接 |
| 查證來源 | record ID、來源筆記、事件或 registry 證據；不放機器 ID wikilink |

每條正式線要能回答「把這兩個想法放在一起，如何幫助思考」。只能說相關時，保持候選。不同使用者從自己的資料形成內容，不安裝預先寫好的個人橋接、品味故事或來源筆記。

#### 關係詞表與跨來源橋接

關係詞表 `RELATION_TYPES`（`tools/ingest_plan_contract.py`，唯一定義）供新來源的同源 `relations[].relation` 與跨來源 `prior_knowledge.cross_links[].relation_type` 共用；registry 既有的自由字串列保留為歷史資料，不改寫。各值與上表小節（概念頁小節）及來源成果頁計數用詞的對應：

| `RELATION_TYPES` | 語義 | 對應小節（概念頁） | 成果頁計數用詞 |
|---|---|---|---|
| `supports` | 新材料加強舊判斷 | 支持 | 鞏固 |
| `contradicts` | 新材料推翻舊判斷，或舊判斷推翻新材料 | 反例 | 反駁 |
| `limits` | 新材料說明舊判斷的成立邊界，或反過來 | 限制 | 限制 |
| `extends` | 新材料把舊判斷帶到新問題或新方法 | 延伸 | 延伸 |
| `tradeoff` | 兩者要選擇、平衡或排序 | 形成取捨 | 形成取捨 |
| `applies_to` | 舊知識是新材料的具體使用場景，或反過來 | 可應用於 | 可應用於 |

「成果頁計數用詞」是來源成果頁「這份來源對舊知識的作用」區塊的計數（`tools/outcome_page_generator.py` 的 `PRIOR_EFFECT_LABELS`）；概念頁小節與成果頁計數是兩個畫面，用詞不同，兩者都以此表為準。

新來源入庫時的跨來源橋接只經 [Compiler §7.2](Compiler%20Worker與Knowledge%20Package設計及驗收規格.md#72-三種來源的程式工作單) 的 `prior_knowledge` 路徑成立，並以正式 source 為支撐：新方依據是本來源 block 的逐字引文，舊方依據是目標知識點本身所載的原證據。`insight` 必須說出兩者放在一起帶出的推理增量，不是任一方 claim 的改寫或泛論；不能以同詞、同題材為由連線，只能說相關的保持候選。

程式能強制甚麼、只能抽查甚麼，以 [Compiler §7.2](Compiler%20Worker與Knowledge%20Package設計及驗收規格.md#72-三種來源的程式工作單)「舊知識連結」第 6 項為準（唯一定義），此處不重複。

#### 舊知識候選的評分

`related_prior_candidates`（`tools/obsidian_idea_retrieval.py`）用自己的評分，與 `best_note_for_query`／`score_concept_note`（think 與星雲使用，不變）分開。候選的數量、編號與決定規則見 [Compiler §7.2](Compiler%20Worker與Knowledge%20Package設計及驗收規格.md#72-三種來源的程式工作單)，此處只寫評分語義。數值都是該模組的具名常數：

- 語料：兩種文件，各算一份文本。概念文件：每個有效、且有 linked 舊 KP 的概念，內容是概念名（算 `PRIOR_NAME_WEIGHT` 次）、一句話，以及它所有 linked 舊 KP 的 claim。無概念 KP 文件：每個 linked、且不被任何有效概念筆記的 `knowledge_point_records` 收錄的 KP，內容是該 KP 的 claim 加來源標題（標題讀自 `sources.jsonl` 指向的來源筆記，取不到就只用 claim）。本來源的概念與 KP 不入語料；被概念收錄的 KP 只經概念出現，不另成文件。候選的 `kind` 對應這兩種：`concept`、`kp`。
- 斷詞：中文用字元 bigram，英文用小寫詞（兩個字元以上）；先去掉 URL 與 `PRIOR_STOPWORDS`（研究、支持、可能等常用詞）。
- 計分：以語料算 IDF，再以 BM25-lite（`PRIOR_BM25_K1`、`PRIOR_BM25_B`）計分；沒在語料出現過的詞，權重按語料中最罕見的詞計。
- 正規化：除以該 query 自身的 IDF 質量，但質量夾在 `PRIOR_QUERY_MASS_FLOOR` 至 `PRIOR_QUERY_MASS_CAP` 個「罕見詞」之間（罕見詞指只出現在一個文件的詞），結果截在 0–1。下限避免只有一個字詞的小標題獨自拿高分，上限避免長 claim 被一般詞稀釋。一個候選取各 query 中的最高分。
- 門檻：`PRIOR_LINK_MIN_SCORE` 是 0–1 正規化分數的強度標示；它不決定候選是否列出，也不決定要否交代。交代規則只見 Compiler §7.2「舊知識連結」第 4 項的 `prior_knowledge_decision_missing`。

校準只可用可重建的合成資料；不把私人工作區、來源識別碼、個人名稱或歷次表現數字寫入可攜規格。詞面分數只作輔助排序，不能取代逐項的來源核對與理由。

#### 概念頁的跨來源區塊

概念筆記由 `obsidian_vault_adapter.py` 的 `build_reusable_concept_entries` 與 `render_concept_note` 從 registry 投影（只讀 registry，不讀計劃，也不是內容真源）。概念有資料時才多出下列區塊；沒有資料的概念筆記位元組不變，也不因此被重寫：

| 區塊 | 資料來源 | 格式 |
|---|---|---|
| `## 目前理解（AI 解讀）` | `_registry/knowledge_points.jsonl` 中屬此概念、`status` 為 `linked`，且帶 `concept_name` 與 `understanding_delta` 的列 | 先寫「最新：」一條，再寫「歷次修正（舊至新）」；排序依列的 `updated`、來源編號、`kp_uid`。每條附來源編號與工作編號（plain text），並標明是 AI 解讀、不是原文引述 |
| `## 支持`、`## 反例`、`## 限制`、`## 延伸`、`## 形成取捨`、`## 可應用於` | `_registry/relations.jsonl` 中 `scope` 為 `cross_source`、`status` 為 `linked`，且兩端知識點都是 `linked` 的列；對應見上表（`contradicts` 寫在「反例」） | 每行寫方向語句、對方概念、`insight` 與雙方來源編號（plain text）。對方有概念頁就用概念 wikilink，沒有就寫其知識點原文、不連線。一條關係在兩端的概念頁都出現並寫明方向，例如「此概念被 X 反駁」與「此概念反駁 X」；`tradeoff` 兩端同寫「與 X 形成取捨」；兩端同屬一個概念時只寫一行「同一概念內，新來源…舊來源」，不連向自己 |
| `## 來源數` | 此概念筆記 `source_records` 的不同來源數 | 有以上任一區塊時才寫 |

入庫寫入時，除本來源的概念外，本來源參與的跨來源關係另一端所屬的舊概念也重新投影，範圍止於這些概念，不擴大至全庫。關係被撤回、被取代，或任一端不再是 `linked` 時不投影，重新投影後該區塊隨之消失。

舊格式生成概念頁（不屬 `build_reusable_concept_entries`、沿用舊 frontmatter 的生成頁）同樣顯示上述跨來源小節，也同樣可作為對方概念被 wikilink 連到。知識點與概念頁的對照只有一個來源：概念頁 frontmatter 的 `knowledge_point_records` 加 `prior_kps_for_note`，實作為 `obsidian_idea_retrieval.py` 的 `concept_notes_with_kp_uids`，與候選檢索共用。舊格式頁只接上或取代尾端這組區塊，其餘位元組（包括 frontmatter 的 `updated` 與行尾）一律保留；沒有資料的舊格式頁位元組不變。手寫概念頁（非生成頁）不會被改動，只可能成為連結目標。經 `render_only` 的內容修訂不處理舊格式頁。

#### 知識方向訊號與思考使用

`tools/knowledge_direction.py` 是唯一責任模組，從 registry 與概念筆記推算，零 AI、零寫入、同一資料與同一日期輸出相同。範圍是 `concept_notes_with_kp_uids` 的有效概念頁（新格式、舊格式生成頁及手寫頁），有 `linked` 知識點者；知識點對概念頁的對照只用這一個來源，同一知識點出現在多頁時各頁都計入。每個概念有：`concept_name`（取頁的 `concept` 欄位）、`source_count`（概念內 `linked` 知識點的不同來源數）、`evidence_source_count`、各 relation_type 的數量、`last_link_at`。關係數量只計 `scope` 為 `cross_source`、`status` 為 `linked`、兩端知識點都是 `linked` 的列（與上表的概念頁投影同一組列）；兩端都屬同一概念只計一次，日期取該列的 `updated`。四類訊號的門檻是該模組的具名常數，也隨 JSON 輸出：

| 訊號 | 條件 | 次序（同分以概念名） |
|---|---|---|
| `evidence_building` 證據累積 | `evidence_source_count` ≥ 3 且 `supports` ≥ 1 | `evidence_source_count`＋支持數，多者先 |
| `tension_hotspot` 張力熱點 | `contradicts`＋`limits` ≥ 1，且 `supports`＋`extends` ≥ 1 | 兩邊關係數之和，多者先 |
| `recent_growth` 近期增長 | 過去 30 天內有跨來源關係的 `updated` | 近期關係數，多者先 |
| `isolated_new` 孤立新概念 | `source_count` = 1、沒有任何跨來源關係，且建立未超過 30 天（建立日取概念內最早的知識點列 `updated`，沒有才用概念筆記 `updated`） | 較新者先 |

`evidence_source_count`：概念本身知識點的來源，加上經 `supports` 或 `extends` 跨來源關係連入或連出的對方來源，取聯集後的不同來源數；`contradicts`、`limits`、`tradeoff`、`applies_to` 不計。理由：舊概念多數只有一個自身來源，證據是經跨來源關係累積的，只計自身來源，樞紐概念永遠不會出現。`source_count` 保持原意，`isolated_new` 仍用它。JSON 每個概念及每個訊號項目都帶 `evidence_source_count`，`schema_version` 不變。

輸出每類前 N 個（預設 5）。入口是 `python -B tools/knowledge_direction.py --root . --json`（可加 `--top`、`--today YYYY-MM-DD`、`--concepts`）；`tools/brain.py` 屬共用面，沒有 `direction` 子命令。首頁區塊、`research-program next` 的 `direction_suggestions`（`tension_hotspot` 與 `evidence_building` 各前 3 個，只作建議，不建立主題、不改研究狀態）和下述 think 規則都讀這個模組，不另算。

`$brain-think` 的 `prepare` 在 packet 加 `established_tensions`：取選中來源（材料候選，加上 `--note` 路徑中的來源編號）的知識點，及這些知識點所屬概念內的全部知識點，找出已成立的跨來源關係，類型限 `contradicts`、`limits`、`tradeoff`。每條有 `relation_id`、`relation_type`、`insight`、雙方 `kp_uid`、雙方來源編號與來源筆記路徑；涉及選中來源的先排，最多 6 條，其餘數量見 `established_tensions_omitted`（只計超過 6 條上限而未列出的）。張力按此次序逐條成對納入：該條雙方來源筆記中尚未在必讀清單內的（空路徑不計），須全部放得進本次讀取上限的剩餘名額，才納入並加入 `required_paths`；名額 = 讀取上限（一般 10；只有一個材料候選而必讀不超過 16 份時為 16）減去去重後的使用者筆記與材料候選必讀數目；雙方筆記已在清單內的一律納入，不佔名額。放不下的整條不納入、也不加任何路徑，改列於 `established_tensions_over_capacity`（每條有 `relation_id`、`relation_type`、`insight`、`concept_names`、雙方來源編號），並在 `gaps` 加一項 `reason` 為 `established_tensions_over_capacity` 的紀錄（`message` 說明未納入的條數及不可聲稱已回應這些張力，附 `relation_ids`），`reading_tasks` 也補一句提示；這不令 packet 變成 `needs_review`。已納入的張力雙方筆記仍須實讀，仍適用 `required_source_bundle_incomplete` 規則；使用者筆記或材料候選本身超出上限，照原有規則處理；既有 `candidate_only` 相鄰探索不變。packet 有 `established_tensions` 時，answer 須有 `tension_responses`：每條張力一項 `{relation_id, response}`，用一句話交代在甚麼條件下哪一方成立；缺少、空白、不認得的 `relation_id`（含只列於 `established_tensions_over_capacity` 的未納入張力）、重複或未涵蓋 `established_tensions` 全部張力時，`answer` 與 `intake-handoff` 以錯誤碼 `tension_responses_missing`、`tension_response_invalid`、`tension_response_uncovered` 拒絕。沒有張力時行為不變。

#### 舊庫回填（P4）

跨來源關係只在新入庫時產生，舊庫的來源之間因此幾乎沒有互相連起來；另有一批知識點沒有歸入任何概念（下稱「未歸概念知識點」）：舊 schema 沒有 `reusable_concept_*` 欄位的，和新 schema 判為 `not_reusable`、附原理由的。`tools/concept_backfill.py`（只屬 OPS）按主題或全庫分批補上。判斷由 AI 填決定檔，程式負責分組、列出候選、驗證、寫入與讀回，不替 AI 判斷有沒有洞見。常數與錯誤碼只在該模組定義，此處只寫語義與數值。

| 命令 | 寫入 | 作用 |
|---|---|---|
| `themes [--top N] [--json]` | 零 | 列出主題組：組編號、種子、成員名、來源數、未歸概念知識點數、組內跨來源相似對數 |
| `prepare --seed "<概念名或 kp_uid>" [--size 20] [--job-id ID]` | 只寫 `outputs/concept_backfill/<job_id>/`，不寫 vault | 以種子決定一個主題組，寫出 `work_order.md`、預填結構的 `decisions.draft.json`、`job.json`（成員、候選對、`prior_registry_fingerprint`、會被改寫的 KP 筆記 sha256） |
| `plan-all --batch-id ID [--chunk-pairs 40]` | 只寫 `outputs/concept_backfill/`，不寫 vault | 全庫候選對一次劃分成互不重疊的 chunk，每個 chunk 是一個普通 job（`<batch-id>-C01…`），另寫 `<batch-id>/manifest.json`；見下「全庫分批」 |
| `check --job-id ID --decisions FILE` | 零 | 列出 issues，格式同 `check-plan`；通過才可 apply |
| `apply --job-id ID --decisions FILE --dry-run` | 零 | 列出每一項會發生的寫入：新增的 relation 行（完整 JSON）、KP 筆記的 frontmatter 差異、registry 行差異、會重繪的概念頁、同概念報告 |
| `apply --job-id ID --decisions FILE` | 寫入 vault 與 `receipt.json` | 寫入後重繪涉及來源的概念頁 |

**成員與主題組。** 成員是有效概念頁（`concept_notes_with_kp_uids` 中有 linked 知識點者，含新格式、舊格式生成頁與手寫頁）和未歸概念知識點。成員之間用 `PriorIndex`（見上節「舊知識候選的評分」）以成員文字互查，只比較跨來源的成員（兩者同屬一個單一來源不算）。主題組由種子以決定性方式長出：每次加入與組內相似度總和最高的成員（同分取較前者），至組大小 `BACKFILL_GROUP_SIZE`（20）為止；兩成員相似度達 `BACKFILL_GROW_MIN_SCORE`（0.15）才互相拉入；一組須涵蓋至少 `BACKFILL_GROUP_MIN_SOURCES`（4）個來源。`themes` 按組內跨來源相似對數（相似度達 `BACKFILL_THEME_PAIR_MIN_SCORE`，即 `PRIOR_LINK_MIN_SCORE`）排序，一組的種子不可已在先前列出的組內，且與先前各組重疊的成員不可超過本組成員的 `BACKFILL_THEME_OVERLAP_MAX`（一半）。同一份資料輸出相同。

**候選對。** 每個成員在組內取來自其他來源、分數大於 0 的前 `BACKFILL_PARTNERS_PER_MEMBER`（3）個夥伴，去重後即為候選對，按分數由高至低編號 `P-01…`，上限 `BACKFILL_PAIR_MAX`（60）。已有 linked 跨來源關係的配對照樣列出並標明「已有關係：類型」；之後的 link 若與該行相同（同一端點、同一類型）以 `backfill_link_exists` 拒絕，不覆蓋其他工作的行。

**決定檔。** `{job_id, links, same_concept, not_linked, loose}`，每個候選對必須有一條 link、或一條 `same_concept`、或被一條 `not_linked` 點名（寫在 `pair_id`，或在 `reason` 寫出 pair 編號）。

- `not_linked`：一條最多點名 `PRIOR_DISMISS_GROUP_MAX` 個，`reason` 去掉編號與標點後最少 `PRIOR_DISMISS_REASON_MIN_CHARS` 字；兩個常數取自 `ingest_rail.py`，不重新定義。
- `links`：`{pair_id, from_kp_uid, to_kp_uid, relation_type, insight, confidence}`。兩個 kp_uid 分屬該候選對的兩個成員、來自不同來源；方向明確，`from` 對 `to` 成立該關係（例如「from 限制 to」），方向相反是另一條關係；`relation_type` 在 `RELATION_TYPES` 內；`confidence` 為高／中／低；`insight` 的改寫重疊與泛論檢查與入庫相同：兩者都呼叫 `ingest_rail.py` 的 `relation_vocabulary_issues` 與 `insight_content_issues`（入庫的 `prior_knowledge_issues` 同樣呼叫，行為與錯誤訊息不變）。同一個 job 的 link 上限 `BACKFILL_LINK_SAFETY_LIMIT`（30），只作防止失控。
- `same_concept`：`{pair_id, reason}`，只報告「這兩個成員是同一個概念」，不搬頁、不合併，不產生任何寫入；列在 dry-run 與 receipt，作為日後合併的依據。
- `loose`：每個未歸概念知識點都要有一項決定。`new_concept`：填 `name` 與 `reason`，`name` 不可與任何現有概念頁同名（相同請改 `join_concept`），也不可與現有頁面只差大小寫、空白或標點，或落在同一個筆記檔名；`join_concept`：只可加入**新格式**概念，`name` 須完全相同，必填 `understanding_delta`；舊格式頁與手寫頁不可加入，因為拼接做法不改舊頁的 frontmatter，遇到它們只可用 link；`leave_loose`：填 `reason`。原來是 `not_reusable` 的知識點改判為 `new_concept` 或 `join_concept` 時，`reason` 須說明為何推翻原判（至少 `BACKFILL_OVERTURN_REASON_MIN_CHARS`（12）字，且不是重複原理由）。

**寫入。** 只經現有的 owner，順序是 KP 筆記、已完成來源的完成後修訂（見下）、registry、概念頁，全部在 vault 寫入鎖內。

- relation 行：與入庫的跨來源行同一格式，由 `obsidian_vault_adapter.py` 的 `cross_source_relation_row` 組裝（入庫與回填共用），`source_id`、`source_identity`、`source_note_path` 取 from 方來源（讀 `sources.jsonl`），`evidence` 取 from 知識點筆記的 `source_anchor`（超過 `BACKFILL_EVIDENCE_MAX_CHARS`，即入庫字幕證據的 700 字上限時，截斷並加「…」），`job_id` 為回填 job，並新增 `origin: "backfill"`（入庫寫的行沒有此欄）。已撤回的行不復活；內容不變不重寫。
- 未歸概念知識點的 `new_concept`／`join_concept`：在 KP 筆記 frontmatter 寫入或改寫 `reusable_concept_status: reusable`、`reusable_concept_name`、`reusable_concept_reason`，其餘位元組（含行尾）不變。**權威是 KP 筆記的 frontmatter**：概念歸屬由 `reusable_concept_decision` 讀它，`knowledge_points.jsonl` 不存這三個欄位，所以 `new_concept` 只改筆記；`join_concept` 另在該知識點的 registry 行寫入 `concept_name`、`understanding_delta`、`job_id`（與入庫相同，概念頁「目前理解」讀它）。
- 已完成來源的 KP 筆記：上一項改寫的 KP 筆記若屬已完成 v2 來源，**只可經唯一寫入口改寫並登記完成後修訂，不可單獨改寫筆記**。如何判斷屬於已完成來源，以及寫入口的行為、錯誤碼、拒絕條件與容許的例外，只在[Compiler Worker 規格〈已完成成果的目前修訂與跨平台讀回〉](Compiler%20Worker與Knowledge%20Package設計及驗收規格.md#已完成成果的目前修訂與跨平台讀回)定義，此處不重複。回填的對應：筆記由 `write_notes` 呼叫寫入口的 `stage_kp_notes` 暫存、由 `register_completion_revisions` 呼叫 `register_kp_notes` 登記並讀回、失敗時由 `restore_notes` 呼叫 `restore_kp_notes` 放回，不改原完成收據與知識內容，並與筆記寫入同一個交易：寫入筆記後，每個受影響的來源接受一份修訂紀錄，內容是該來源已接受的全部改動加上本 job 的筆記（變更路徑、收據記錄的原雜湊、現有位元組雜湊；前一指標 hash）；理由是「P4 概念回填加入 reusable_concept_* 欄位（不改知識內容）」，若筆記原已有判斷而被改寫，理由附註此事（中斷後重跑時，原判斷取自 job 在 `prepare` 時記下的 `loose.prior_status`，附註與不中斷時相同）；證據是本 job 在寫入前保存、之後不再改寫的 `apply_state.json`，其 `after.notes` 記有該筆記寫入後的 sha256，登記前須與現有位元組相同。登記後立即以 `completed_receipt_readback` 讀回該來源。任何一步失敗，已登記的紀錄與指標全部撤銷、本次執行寫入的筆記放回原狀（先前中斷的執行已寫入的筆記不動，由再跑補登記），registry 與概念頁不寫，以 `backfill_completion_revision_failed` 報錯，修好原因後重跑同一個 apply。寫入口拒絕的情況（其 `completed_kp_*` 碼附在訊息內）：寫入前即以 `backfill_completion_revision_unsupported` 拒絕（dry-run 同樣拒絕），改走內容修訂流程或讓該點維持未歸類。
- 概念頁：apply 最後呼叫 `project_concept_graph_for_sources`，範圍是所有涉及的來源；兩端的新格式頁與舊格式頁都顯示關係，未歸概念知識點生成新概念頁。
- 星雲：投影後若 vault 已安裝星雲（`outcome-pages/nebula.html`），呼叫 `knowledge_nebula.refresh_installed`；沒有安裝則不做。結果的詞彙見 §3「思考紀錄保存」的星雲收據詞彙；任何結果都只記在 receipt，不使已寫入的回填失敗。
- `receipt.json`：`execution`（`check_seconds`、`writes_seconds`、`projection_seconds`、`apply_seconds`，以及 `prepare_to_apply_seconds`）、各類數量、同概念報告、改動檔清單（路徑與前後 sha256）、`completion_revisions`（實際登記的來源、修訂紀錄路徑與雜湊、筆記路徑；沒有已完成來源時為空）、星雲結果。

**可重跑與拒絕。** dry-run 在 系統暫存位置的一次性副本（只含 apply 會動的檔案）上執行與 apply 相同的程式，所以列出的寫入與實際 apply 完全一致，用完即刪，不寫 vault 與 job；已完成來源的修訂不在副本內登記，dry-run 以 `completion_revisions` 列出會登記的來源與筆記，並與 apply 同樣拒絕接不住的情況。apply 在以下情況拒絕且零寫入：check 不通過（check 以當前 vault 重跑，見下「全庫分批」的防護收窄）；要改寫的 KP 筆記 sha256 與 prepare 時不同，或 link 端點已不再是 linked（`backfill_stale_job`）；已完成來源的筆記接不住修訂（`backfill_completion_revision_unsupported`）。apply 開始寫入前先把決定檔的 sha256 與預期結果記入 job 內的 `apply_state.json`：同一個 job id 中斷後再跑會接著做（檔案落在寫入前或寫入後的狀態都接受，其他狀態拒絕；已寫入而尚未登記修訂的已完成來源筆記，由再跑補登記），並改用另一份決定檔也拒絕；全部完成後（有 `receipt.json`）重跑同一個 apply 回報 `already_applied`，零寫入。KP 筆記與 registry 寫入保留原行尾；registry 必須是標準形式（每行一個鍵排序的 JSON、依主鍵排序），否則以 `backfill_registry_not_canonical` 拒絕。

錯誤碼（每條規則各一個）：`backfill_job_id_mismatch`、`backfill_section_invalid`、`backfill_pair_unaccounted`、`backfill_pair_unknown`、`backfill_pair_conflict`、`backfill_dismiss_group_too_large`、`backfill_dismiss_reason_thin`、`backfill_link_direction_missing`、`backfill_link_endpoint_not_in_pair`、`backfill_link_same_source`、`backfill_link_insight_missing`、`backfill_link_confidence_invalid`、`backfill_link_duplicate`、`backfill_link_limit`、`backfill_link_exists`、`backfill_link_evidence_missing`、`backfill_source_unknown`、`backfill_same_concept_reason_thin`、`backfill_loose_unaccounted`、`backfill_loose_unknown`、`backfill_loose_duplicate`、`backfill_loose_decision_invalid`、`backfill_loose_name_missing`、`backfill_loose_reason_missing`、`backfill_loose_name_exists`、`backfill_loose_name_near_duplicate`、`backfill_loose_name_collision`、`backfill_loose_delta_unexpected`、`backfill_loose_background`、`backfill_join_target_missing`、`backfill_join_legacy_page`、`backfill_join_delta_missing`、`backfill_overturn_reason_missing`、`backfill_kp_note_missing`、`backfill_kp_note_unwritable`、`backfill_stale_job`、`backfill_registry_not_canonical`、`backfill_completion_revision_unsupported`、`backfill_completion_revision_failed`、`backfill_projection_failed`；與入庫共用的 `relation_type_invalid`、`cross_link_insight_paraphrase`、`cross_link_insight_generic`。

命令層級的拒絕：`backfill_seed_not_found`、`backfill_seed_ambiguous`、`backfill_theme_too_narrow`（組不足 4 個來源）、`backfill_job_id_invalid`、`backfill_job_exists`、`backfill_batch_exists`（`plan-all`：批次資料夾或任一 `<batch-id>-Cnn` 已存在）、`backfill_job_missing`、`backfill_vault_mismatch`、`backfill_vault_unreadable`、`backfill_file_unreadable`、`backfill_size_invalid`（`--size` 或 `--chunk-pairs` 不合規）。`plan-all` 的 batch id 不合規（含超過 60 字）用 `backfill_job_id_invalid`。

程式能強制結構、端點真實存在、詞表、不可只改寫、可重跑與不復活；「這條線是否真的有洞見」的抽查邊界與入庫相同，見 [Compiler §7.2](Compiler%20Worker與Knowledge%20Package設計及驗收規格.md#72-三種來源的程式工作單)「舊知識連結」第 6 項。

##### 全庫分批

主題組之間的成員最多重疊一半，同一對會被重複判斷。全庫分批把全庫的候選對一次劃分成互不重疊的小批（chunk），每對只判斷一次；所有 chunk 由同一個 vault 狀態一起準備、一起交給 AI 判斷，之後逐個 apply，期間不用重新 prepare。命令：`plan-all --batch-id ID [--chunk-pairs 40]`，只寫 `outputs/concept_backfill/`，零寫 vault。

**候選對與門檻。** 每個成員取來自其他來源、分數大於 `BACKFILL_PAIR_MIN_SCORE`（0.0）的前 `BACKFILL_PARTNERS_PER_MEMBER`（3）個夥伴，去重後即為全庫候選對（與主題組同一規則，範圍是全庫）。門檻依據：試點中最低分的真實連結是 0.109，設 0.1 的門檻會貼近真實連結；否決一對的成本只是一句 `not_linked` 理由，所以只要求分數大於 0。先取夥伴、後扣除下述排除的對，所以被排除的對不會讓排名較後的夥伴遞補。

**pair 的身份與排除。** pair 的身份是兩個成員的 key 排序後的組合；key 在概念頁是它的 vault 相對路徑（`concepts/<名稱>.md`），在未歸概念知識點是 kp_uid。下列兩類不列入，數量記在 manifest：

- 已有關係：兩端之間已有 `linked` 的 `cross_source` 關係，入庫或回填皆計；成員是概念時，以其任一知識點計。已撤回的行不計。
- 已決定：同一 vault 中 `receipt.json` 為 `applied` 的 job，其 `job.json` 內的全部 pair。依 check 的規則，一個通過的 job 每個 pair 都有 link、`same_concept` 或被 `not_linked` 點名，所以 applied job 的 pair 全部已決定；清單由各 job 的 `job.json`（成員 key 與 pair）推算，不另建檔案。只 prepare 或 plan-all 而未 apply 的 job 不算。

已知缺口：未歸概念知識點經 `join_concept` 之後，key 由 kp_uid 變成概念頁路徑，之前決定過的 pair 身份因而對不上，下一輪 plan-all 可能再列出這類 pair（多判斷一次，否決成本很低）；同一輪之內不會發生，因為所有 chunk 同時產生。

**chunk 劃分。** 決定性的鄰域增長：pair 按（分數降序、身份）排序；取分數最高的未分配 pair 作起點，然後反覆加入與 chunk 成員共用至少一個成員的未分配 pair（取分數最高者），直至 `--chunk-pairs`（預設 `BACKFILL_CHUNK_PAIRS` 40，不可超過 `BACKFILL_PAIR_MAX`）或沒有相鄰的 pair 為止，再開下一個 chunk。chunk 之間的 pair 互不重疊，合起來等於全部候選對扣除排除的對。起點總是剩下的最高分，所以 chunk 依其最高分降序編號：job id 為 `<batch-id>-C01`、`C02`…（chunk 數達 100 以上時位數加寬）；batch id 最長 `BATCH_ID_MAX_CHARS`（60）字，其餘規則同 job id。每個 chunk 是一個普通 job 資料夾（`job.json`、`work_order.md`、`decisions.draft.json`，格式同 prepare）：成員是 chunk 各對涉及的成員，`pairs` 是 chunk 的對，標明「已有關係」的做法不變；`job.json` 另有 `batch`（`batch_id`、`chunk_id`）與 `loose_decision_count`，`seed.query` 為 `plan-all <batch-id> <chunk_id>`。

**loose KP 唯一決定。** 每個未歸概念知識點只在它最先出現的 chunk（按 chunk 編號）要求決定；其他含它的 chunk 把它列為背景（成員有 `background: true`，work order 寫「只作背景，不在此 chunk 決定」）：不出現在 draft 的 `loose`、不要求決定、不納入 `guard`，仍可作 link 的端點。決定檔仍為背景知識點寫了 `loose` 項時，以 `backfill_loose_background` 拒絕。

**收窄後的防護（單一主題 job 同用，不設兩套）。** apply 與 check 只固定一件事：本 job 要改寫的 loose KP 筆記（`guard.notes`，只含本 job 要決定的點）的 sha256 與 prepare 時相同，不同即 `backfill_stale_job`（自己記錄在 `apply_state.json` 的寫入結果除外）。不再要求 `prior_registry_fingerprint`、`knowledge_points.jsonl`、`relations.jsonl`、`sources.jsonl` 的雜湊或概念頁集合與 prepare 時相同。取而代之，apply 以**當前 vault** 完整重跑 `check_all`（取得鎖後再算一次 plan，所以寫入依據的是寫入開始時磁碟上的 registry 行），包括：link 端點仍是 `linked`（否則 `backfill_stale_job`）；`backfill_link_exists`；`new_concept` 的名稱已被佔用，包括另一個 chunk 剛建立的概念，以 `backfill_loose_name_exists` 拒絕並提示改用 `join_concept`；`join_concept` 的目標仍是新格式概念。已開始 apply 的 job（有 `apply_state.json`）的名稱檢查改用 prepare 時的快照，因為它自己的寫入已是 vault 的一部分。冪等不變：已套用的 job 重跑回報 `already_applied`、零寫入，中斷後可續做。

**manifest。** `outputs/concept_backfill/<batch-id>/manifest.json`，寫在所有 chunk 之後（沒有 manifest 的資料夾不算批次），不含時間戳，同一 vault 狀態輸出相同（各 chunk 的 `job.json` 另有 `created_at`、`prepared_epoch`）。內容：`schema_version`、`batch_id`、`vault`、`prior_registry_fingerprint`（紀錄用，不再是 apply 的條件）、常數、`member_count`、`candidate_pairs`（排除前總數）、`excluded`（`existing_relation`、`already_decided`、`decided_from_jobs`）、`planned_pairs`、`chunks`（`chunk_id`、`job_id`、`pairs`、`members`、`loose_decisions`、`max_score`、`min_score`）、`loose_decisions`，以及 `limitations`：`loose_without_candidate_pairs` 列出沒有任何候選對的未歸概念知識點，作為限制列出，不為它們另建 chunk；`decided_key_gap` 說明上述已知缺口。同一個 `--batch-id` 的資料夾，或任何 `<batch-id>-Cnn` 資料夾已存在時，以 `backfill_batch_exists` 拒絕且零寫入。

## 3. 問我的第二大腦：正式對話入口

這是由當前 AI 執行的取證與推理工作流程。Python 負責材料、引用、版本及閱讀產物，不自行創造洞見。此節是「問我的第二大腦」完整論證與內含思考地圖的推理要求唯一責任位置；閱讀頁、樣式與導航見成果頁設計 §5。

問答封包遇到 `sources/` 的 `Canonical article body` 正文保存區超過 `PACKET_BODY_MAX_CHARS`（20,000）字元時，會保留標題與其餘內容，以固定提示投影，並保存原檔雜湊供覆核。`## 資訊圖` 段（資訊圖製作紀錄，含原文實體錨點摘錄）超過同一上限時亦以固定提示投影；該段多於一個時列 `infographic_section_not_delimited` 缺口，不作此投影。

#### 用途與價值

深度提問要做到三件事：讓多份材料暴露原本未被提出的問題；把事件或理論拆至可說明的因素、限制和作用機制；轉換觀察層次，帶出能回頭改變原問題的相鄰觀點。問答交付完整論證和離線互動 HTML，內含可追溯的思考地圖，讓人檢視同一套推理。

「新」指相對本次已讀材料的推理增量，不聲稱世界首創。同詞、同題、摘要重排、換句話說、把既有結論冠上新名稱，以及「要平衡／持續學習」等泛論均不算增量。應說明拿走任何一項主要材料後，哪一步推導會失去依據；若無差別，該材料只是背景，不能冒充碰撞。

拆到底層時，要分清觀察、來源解釋和 AI 假設；列出作用者、資源或誘因、限制及中間環節，問「哪個因素改變，結果可能怎樣不同」。比喻、時間先後、個人回憶或一篇觀點文章不足以證明因果。第一原理不是寫上「本質」便成立；找不到根因時交付競爭假說及最能分辨的觀察。

相鄰探索須明示從哪個框架換至哪個框架、何以能跨過去，以及回來後會改變甚麼判斷。不得只列相似主題或多幾篇可讀文章。推理可產生設計提案、可反駁假說或有條件推導，不必每次推出單一建議，也不為填滿版面硬湊三項。沒有足夠增量時，說清卡在哪個假設或缺口；簡單找事實可按引用短答，不強行使用完整深度格式。

#### 取證、推理與交付

1. `python -B tools/ask_second_brain.py prepare --question "問題"`。需要指定或反方來源，可重複 `--note "sources/…md"`；每次必須二選一：重複 `--expand "…"` 提供同一問題的另一語言、同義說法或來源／框架正式名稱，或以 `--no-expand "理由"` 記錄不需另一說法的原因。`--expand` 去空白後不可為空、不可與原問題只差大小寫、全半形或首尾標點，重複項保留第一次，最多 5 個；兩種選項不可同時使用。原問題仍是唯一要回答的問題，packet 的 `query_expansions` 保留選材提示，並以 `expansion_decision` 記錄 `expanded`（數量）或 `declined`（理由）；內部直接呼叫沒有決定時記為 `not_recorded`。AI 已完成針對性選材而加 `--selected-only` 時不要求這項決定，記為 `not_applicable_selected_only`；若仍給 `--expand` 照常選材及記錄。工具先納入指定筆記，再由既有素材及 idea retrieval 取材。每個材料候選的必讀（`must_read`）是來源筆記（已載知識點草案）、應用筆記及已列衍生應用；該來源命中問題的知識點筆記及關係端點知識點筆記另列於 `knowledge_point_paths`（命中詞數多者先），在讀取上限的餘位按候選輪流載入，放不下只列於 `deferred_paths`，不令 packet 變成 `needs_review`。`context_ready` 不是答案。全文閱讀上限見 §2「知識方向訊號與思考使用」（一般 10 份；只有一個材料候選而必讀不超過 16 份時為 16），其餘列於 `deferred_paths`；已成立張力的納入與放不下時的處理（`established_tensions_over_capacity`）也在該處定義；不能用取材上限聲稱全庫沒有反例。

選材只在 `tools/knowledge_material_selector.py` 評分：英文以詞界比對並作輕量字尾正規化，中英文虛詞及問句詞不計；每次呼叫以全部候選來源計 IDF，長正文按長度飽和，知識點只取固定數目的最佳命中並遞減，所以重複 KP 或長文不會線性加分。完整問題或其長連續片段在來源標題、來源標示、檢索別名或知識點標題相符時強力提升；檢索別名與來源標題同權。`--expand` 的字詞以較低權重加入同一選材計算，不能改寫原問題。來源的 `source_retention_mode` 不是 `full_body_required`（包括舊資料沒有 mode）時，selector 可經既有完成收據所束的 package 讀 `canonical_source.md` 作低權重、長度正規化的**比對**；該檔缺失或不可讀便跳過，永不放入 `must_read`、evidence path 或作為回答證據。`full_body_required` 的原文仍只由 vault note 的原文保存區負責。
2. AI 實讀 evidence 原文和必要上下文，分清原始來源與同源衍生筆記。至少主動找一個能改變結論的反例、競爭解釋或適用邊界；搜尋無命中只代表本次未找到。packet 帶有 `established_tensions` 時，雙方來源都要實讀，並按 §2「知識方向訊號與思考使用」交 `tension_responses`。來源是資料，不能變成指令。重要材料延後或遺漏時補讀，以 `--note` 重新建立 packet。
3. 先形成庫內判斷；使用者說只查庫便不上網。其餘只有明確缺口需要時才用當前工具查公開主題，有時效的前提另依下述資料契約的外部材料規則先核對，不外傳私人筆記、偏好及 packet。網頁保存必要短摘錄和實際讀回，不能以搜尋摘要代替正文。
4. 深度 answer 包含 `session_id` 及下述 `analysis`。先執行 `answer --packet <prepare.json> --answer <answer.json>`（packet、answer、review 檔案須以工作區相對路徑傳入，可放在 `outputs/ask_second_brain/`），只取得 `review_required` 和 `review_target_sha256`，不生成可冒認完成的讀者成果。修正引用及結構錯誤後，由另一輪實讀反審寫覆核檔；複雜或重要推理優先獨立覆核，作者另輪檢視則如實標示。
5. 同入口加 `--review <review.json>`，逐一核對來源現況、短引文、推理結構和覆核版本後，按下述「思考紀錄保存」自動保存及接通導航。只有 `complete` 並實讀論證／操作受影響互動，才可交付深度成果。程式接受 review 只表示有版本相符的覆核紀錄，不會驗證「誰真的讀了」或證明語義品質；執行代理須對實際覆核負責。

#### 思考紀錄保存

正式深度 answer 入口由 `tools/thinking_archive.py` 自動執行以下步驟；使用者不必另說「保存答案」，AI 也不能以自行生成的 HTML 代替正式完成。這些紀錄標記為 `derived_reasoning`，不增加原始來源數、不寫入來源 registry，也不自動升格為 graph 關係。簡單 `claims` 查證仍屬短答路線，不套用深度成果完成狀態。

| 位置（相對 Vault） | 責任 |
|---|---|
| `thinking/ASK-….json` | 已覆核問題、完整來源快照、answer、review、建立時間與內容指紋；是這份推理紀錄的重建真源。只存相對來源路徑，搬庫後可重建。 |
| `thinking/ASK-….md` | Obsidian 可讀的完整論證與來源筆記連結；由 JSON 建立的配對筆記，已有人手修改便保留並標待核對，不靜默覆蓋。 |
| `outcome-pages/thinking/ASK-….html` | 共用閱讀器生成的互動頁，可返回剛才的篩選位置、成果總覽、思考紀錄、配對筆記及目前來源。來源快照仍保留，供核對當時推理。 |
| `outcome-pages/thinking.html`、`成果頁總覽.md` | 分別提供可搜尋目錄與 Obsidian 入向連結；目錄責任見成果頁設計 §4。 |

相同身份與內容重跑會接續；不同內容、配對筆記已改、路徑越界或存在寫入鎖時拒絕覆蓋。中斷保留已寫紀錄，重跑原命令完成缺少的投影；若遺留鎖，先核實沒有仍在執行的寫入，不能盲目移除。只有保存、目錄唯一列、Obsidian 入口及 HTML 相對導航目標都讀回，正式入口才回 `complete`。保存後自動更新已安裝的星雲（讓這份紀錄以「新想法」星回到星雲，見知識星雲規格的「新星誕生」）；結果只記入收據的 `nebula` 欄位，不回退紀錄；詞彙見下。

星雲收據詞彙（此處是唯一定義；呼叫 `knowledge_nebula.refresh_installed` 的兩個呼叫端各用自己的詞，程式詞彙沒有統一）：

| 呼叫端與欄位 | 已更新或重建 | 沒有安裝 | 暫停 | 失敗 |
|---|---|---|---|---|
| 思考保存：`tools/thinking_archive.py` 的 `refresh_nebula`，收據 `nebula` 欄位 | `updated` | `not_installed` | `paused` | `refresh_failed` |
| 概念回填：`tools/concept_backfill.py` 的 `refresh_nebula`，`receipt.json` 的星雲結果 | `refreshed` | `not_installed` | `paused` | `failed` |

`paused` 是新加的結果：受管的閱讀層程式有未經發布關卡登記的改動（或檢查答不出）時，暫停重建，星雲沒有被重建，所以不算已更新；原因記在同一欄位的 `reason`，失敗則另記 `error`。`not_installed`、`paused` 與失敗都不使已保存的紀錄或已寫入的回填失敗。

刷新入口會從保存紀錄重建頁面，不重新要求 AI 寫 HTML。來源更新或缺失、紀錄或筆記改動均標 `needs_review`；保留歷史快照，不能冒充依最新來源覆核。原 JSON 移除後，已知舊閱讀頁改為失效說明。思考紀錄獨立保存與重建，不依賴白板。

來源及配對筆記連至 `outcome-pages/notes/` 共用 UTF-8 閱讀投影，避免瀏覽器直接讀 Markdown 的編碼差異；可回到原答案、下載原筆記。刷新時依目前原筆記重建，原筆記缺失則顯示失效說明。本機 `file:` 閱讀時，由相對筆記位置產生「在 Obsidian 開啟」連結；需要本機已安裝 Obsidian 並能識別該 Vault。HTTP 預覽不猜用戶磁碟路徑。程式可核對 URI 指向，原生 Obsidian 視窗是否開啟另需實測。後續修改論證須重新取材、覆核並保存新身份；不要手改 HTML 作正式修訂。

#### 獨立閱讀品質

答案須供沒有看過聊天、材料包或檔名的讀者獨立閱讀。先交代討論對象、要作的判斷、已知情境與資料性質；材料是合成例子、自述、評論或實驗，須當場說明。不得把來源沒有交代的時間、角色或背景補成事實。

新答案的 `analysis.reader_context` 含 `title`（讀者用的題名，不照抄操作指令）、`setting`（討論情境、問題與證據限制）及 `materials`（每項 `{id, label, summary}`，對應實際 evidence，說明材料身分及與本題相關的內容）；凡推理用到的材料均須介紹。必要術語才加 `terms: [{term, meaning}]`，以本文具體意思解釋，不用抽象詞換抽象詞。

`thesis` 同時用作目錄摘要，單獨讀亦須知道主體、問題、主要判斷及其條件。不可只寫「三份材料顯示」「如上所述」「這個機制」讓讀者猜指代。正文每一新段及圖上節點核對人物、數量、比較基準與指代；例如「選擇權」要說明此處指先保留活動名額、稍後才決定，而非假定讀者知道作者的比喻。代名詞本身並非禁詞，前文已清楚建立對象便可使用。

新增材料介紹不等於論證已自洽：還須核對結論是否回答原題、各段採用同一個詞義、證據與假設是否混寫、由觀察到結論的中間步驟是否足夠、反方和限制是否被後段推翻。資料不足時具體說明未知，不用流暢文字掩蓋缺口。

覆核者只看原始材料及輸出成品，不能借助作者聊天補意。新答案 review 加 `standalone_check: {verdict, referents, scope, coherence}`（OPS 尚未檢查，見上）：分別記錄能否解出材料與指代、能否分辨資料性質及適用條件、論證與摘要／圖是否一致；寫具體核對理由，`verdict` 為 accept 或 revise。任何 revise 均須修正後重新覆核。機器只檢查欄位、材料綁定與版本，不可能以字數、禁詞或自填 accept 證明理解。

HTML、Markdown 和目錄同用這份已覆核內容。背景及材料介紹預設可見；保留完整原問題及來源快照供查核。歷史紀錄不自動補寫或重簽覆核；缺少這層品質資料時保留原文並標待核對（OPS 的 `thinking_archive.py` 尚未實作此標示），重新取材後以新身份保存修訂答案。

#### `thinking_v2` 資料契約

`analysis` 含上述 `reader_context`（OPS 尚未檢查）、`schema: thinking_v2`、`thesis`（本題整體回答）、`insights`（0–3 個充分展開的判斷）。每項包含：

| 欄位 | 要承載的內容 |
|---|---|
| id、title、kind、claim | 唯一 I 編號、短標題、hypothesis／deduction／design、精確判斷；不得把未證因果寫成既定事實 |
| premises | 至少兩項 `{id, quote, contribution}`：證據 id、原文短引、該材料不可替代的作用；同一來源的 source／APP／KP 不算多源 |
| baseline、delta | 原文已經說了甚麼；本次新暴露的問題／新增推導，不能同義重複 |
| derivation | 有順序的推理文字清單，至少兩步；讓讀者指出哪一步可能跳躍 |
| mechanism、assumptions | 拆到底層的因素／限制／因果環節，及其尚未成立的前提；必要時清楚保留根因未知 |
| alternative、boundary | 能解釋同一現象的實質競爭解釋，及何時不可套用 |
| discriminator | `{observe, supports, undermines}`：具體可做的分辨觀察，以及兩種結果分別如何改變判斷；不能「成立即支持、不成立即推翻」 |
| implication | `{before, after}`：新洞見令哪個問題、選擇或設計發生甚麼具體改變 |
| adjacent | `{question, from_frame, to_frame, bridge, return_value}`：下一問、原框架、新框架、跨越理由、回到原題的價值 |
| map_labels | 新生成深度成果為圖上節點寫簡短、可獨立理解的標籤，每項不超過 40 字。鍵為 before／after／delta／claim／mechanism／assumptions／alternative／observe／from／to／bridge／question／return、p1…（依 premises 次序）、step1…（依 derivation 次序）。只濃縮對應段落，不新增論證；舊答案可省略，閱讀器明示「節錄」並保留全文。 |

沒有新增量時 `insights: []` 並填 `no_increment_reason`；如實交代尚未形成新判斷。簡單查證仍可用舊 `claims`（kind/text/citations）與 `explorations`，結果只為 `citation_checked`，不能冒充深度完成。

外部材料沿用 `web_reason` 和 `web_sources`，每項含 W 開頭 id、url、title、retrieved_at、readback。時效核對與網上延伸都沿用同一組欄位。程式只要求有 `web_sources` 時 `web_reason` 非空，沒有列舉值；星雲指令範本要求時效核對時在 `web_reason` 寫明「核對時效」，向外延伸則在答案文字內引用網上內容處標「網上延伸」，不要求寫進 `web_reason`。引用依實際 evidence id，不得捏造。短引只證明出處，不自動證明支持主張。第一人稱紀錄保留自述及回望時間、研究評論保留評論身份；不能提升成原始實驗證據。

覆核 JSON 含 `target_sha256`（工具回傳值）、`reviewer`、`method`（independent／author_second_pass）、`items`。每項用 `items[].id` 對應一個 insight 的 `id`，寫 `increment_check`、`grounding_check`、`strongest_objection`、`adjudication` 的具體理由及 `verdict`（accept／revise）。任何 revise 均阻止交付。無增量答案改填 `no_increment_check`。來源、問題或 answer 有變便重新讀證據及覆核，不可只更新 hash。

有 `map_labels` 時，每項覆核另含 `visual_check`，逐一核對短標與對應原段落，尤其不能把「可能」省成確定因果，不能把來源貢獻當來源原話；相鄰觀點仍標待探索。短標變動也包含在原 answer 的覆核指紋內。

#### 讀者成品與驗收

深度答案保存為思考紀錄；完整論證與互動思考地圖共用同一份已覆核的 `thinking_v2` 資料，由 `tools/thinking_map.py` 及同名 CSS／JS 生成，從 `thought_analysis.render_html` 進入。預設完整文字，可切換到四種思考角度。使用者毋須填 JSON。

| 讀者看到的視角 | 讀取的內容及可見變化 |
|---|---|
| 問題怎樣改變 | 原先問法 → 新暴露的問題 → 現在值得追問；讓人直接比較本次思考帶來的變化。 |
| 材料在哪裏拉扯 | 每份材料的不可替代作用 → 新問題 → 待驗判斷，並列實質競爭解釋。材料可互補或有張力，不把所有連線硬稱為矛盾。 |
| 背後怎樣發生 | 成立假設、依序推導、可能機制、競爭解釋及分辨觀察；箭嘴表示推導次序，不證明因果。 |
| 走到另一個觀點 | 原框架 → 跨越理由 → 新框架 → 下一問 → 帶回原題的價值；不以相似詞清單代替跨框架推理。 |

桌面用 SVG 呈現關係、右側展開選中節點的完整說明和來源；節點短標來自已覆核內容，來源引文與 AI 對材料的解讀分開顯示。顏色加文字區分證據、推理、條件、競爭解釋與延伸，不用位置、大小或亮度冒充可信度。手機重排成可操作的縱向節點和明示連線清單，不能只把整幅圖縮小。上一個／下一個角度提供有次序的閱讀，也容許直接切換和返回。

問答互動可比較已撰寫的「未觀察／假如支持／假如削弱」情境、跳讀證據及帶走完整追問；切換不會冒充取得新證據、動態重新推理或已向 AI 發問。思考紀錄的深度文字保留全部段落、假設、邊界、引文及來源。沒有 JavaScript 時仍提供文字閱讀。HTML／JS 離線可用，不外傳資料、不回寫筆記；重新整理時從保存紀錄及目前共用樣式重建。

驗收同時覆蓋：原文摘要冒充新洞見、單源多份筆記冒充多源、跳步因果、空泛反方、循環反證、同義相鄰主題、錯引、舊來源及舊覆核。另驗完整答案與來源保留、目錄到內頁及返回路徑。程式檢查來源、schema、精確重複和版本；語義反審處理同義改寫及推理有效性。實頁核對文字／地圖切換、來源跳讀、共同導航、窄屏及返回路徑。機械通過不等於新洞見已證真，不能用自填 passed 代替實讀。

新問題重新取材。選定外部材料入庫仍用 `intake-handoff`，suitability 的 taste／level／claims／novelty 各需 suitable 和理由，再走正式 Compiler Worker 全文及寫入流程。候選、answer 與短摘錄均不是來源全文，沒有正式完成憑證不稱已入庫。

回歸入口：`tests/ask_second_brain.py`、`tests/thought_analysis.py`、`tests/test_thinking_archive.py`、`tests/test_thinking_map.py`；閱讀樣式與導航另見成果頁設計。

## 4. 探索回流與創作

使用者選定一條外部來源要求保存時，先評估 suitability 的 taste／level／claims／novelty 四項，每項需 `decision=suitable` 和 reason。品味合適不等於迎合；有助修正判斷的反方觀點可以適合。程度要考慮前置知識，新增價值不能只是重複已有內容。

用 `ask_second_brain.py intake-handoff --packet <prepare.json> --answer <answer.json> --web-id W1 --suitability <suitability.json>` 形成候選。`candidate_ready_for_ingest` 不代表已入庫；任一不適合則說明理由並停止。已選定且合格才按 [Compiler §7.2](Compiler%20Worker與Knowledge%20Package設計及驗收規格.md#72-三種來源的程式工作單) 的正式來源入口完成，不能把 answer/web摘錄當原文。完成後新的問題重新讀 Vault，才會納入新知識。

比較、報告和創作沿用同一取材與讀證據流程。分清來源主張、跨來源綜合、反方及創作推論；依已知讀者、用途、格式交付成品，不只大綱。創作不改來源筆記，不自動入庫；使用者另行選定才走正式入庫。

## 5. 提取收據及五題驗收

`tools/obsidian_idea_retrieval.py` 只讀 Vault 筆記及正式 registry。單題 `--query "問題" --json` 不依賴固定題庫。輸出保留問題、命中 idea、必讀筆記、record IDs、`問題 → idea → concept → record`、答案材料、弱連結及 bridge backlog。狀態為 `ready`、`needs_bridge` 或 `insufficient_evidence`。

可攜版沒有預填個人問題。使用者需做 workspace 級提取驗收時，在 Vault `_registry/retrieval_questions.json` 保存 JSON array；每項含 `question_id`、`question`、`seed_title`、`purpose`，問題及入口應來自自己的已入庫資料。未配置時回 `not_configured`，不能套入舊使用者題庫或假稱 ready。

正式五題驗收固定五條自選問題，每條 receipt 包含 `question`、`status`、`retrieval_path`、`must_read_notes`、`record_ids`、`answer_summary`、`missing_or_weak_links`、`bridge_backlog`、`generated_at`。至少三題 ready，其餘最多為 needs_bridge；任何 insufficient_evidence 都不能稱提取功能通過。receipt 在 outputs，不放 concepts。合成 fixture 可驗機制，但不代替使用者自己的題庫驗收。

## 6. 週期整理與新增內容接合

`tools/obsidian_semantic_maintenance.py` 只讀 Vault/registry，寫 outputs receipt，不直接修改來源、concepts、APP或registry。預設整理只交最多五項有證據且有使用價值的建議。

週期整理收據保存 graph nodes/edges/components/largest component/orphans/multi-source components、孤立 concept 建議、小來源群缺 hub/tension/taste_signal/application_scene 的情況、`formal_graph_write_allowed=false` 和 recommended_action。建議可包括保留、合併、改名、補 bridge或退役；收據不授權實際刪、移、改名。維護不以孤點歸零或線數成長為目標。

新增來源完成後，APP保留同輪分析的可重用概念／idea 候選、明確無候選或仍待判斷，不能用缺值冒充明確無候選。沒有跨來源洞見、缺個人脈絡或拒絕採納，不等於來源沒有可重用的單源概念。橋接候選須有實際證據及 candidate_only；「未建立」等佔位字、主題相似或採納理由不能充作橋接證據。沒有脈絡便保留未知，不硬湊關係；正式 graph link 只在有證據的整理／人工覆核後寫入。

`--weekly-organize --write-receipts --check --json` 產生整理隊列；獨立核對用 `--new-content-candidates --source-id <id> --output-dir <新候選證據目錄> --write-receipts --check --json`，只選指定來源，不覆寫已綁完成收據的歷史候選。首次入庫與正式修訂均由 rail 自動接合；目前候選的選讀與同交易恢復只見 [Compiler §6](Compiler%20Worker與Knowledge%20Package設計及驗收規格.md#6-workercheckpoint-與完成)，不例行重跑。`--acceptance` 合併整理、新內容候選及已配置五題證據；未配置或沒有足夠真實資料時，不宣稱整個個人知識網 ready。

## 7. Maps、設定與安全

maps 只保留總索引、File Index、知識/品味入口、分層指南與內容目錄；其用途是導覽，不能把所有檔案變節點。`obsidian_semantic_map_flow.py --json` 是唯讀盤點；`--check --quality-limit 9999 --json` 檢查連結、標題、graph setting與健康。

缺少 concept 可以用 `--write-missing-concepts --check --json` 只新增，仍須沿 §2 的已保存判斷；missing／rewrite 不復活被拒或撤回的生成概念。`--rewrite-concepts` 可能覆寫 generated 內容，`--prune-stale-maps` 可能移除 maps；只有具體授權與目標讀回後才可操作，不列為日常整理預設。既有手寫筆記不覆寫。

正式內容修訂只在已核對的 source-local 生成責任內失效派生物，不刪、搬或改名舊檔。只屬該來源的舊 generated concept 標為 `concept_status=superseded`；共享 generated 節點只移除可證明屬該來源的歸屬／舊關係，保留其他來源及不確定內容；手寫節點不動。共用 loader、候選、提取及再生均排除已失效節點，不能只加狀態卻仍讓它作 active 材料。source／KP 表面的 `related_points` 及 Relations 亦須按 registry 的已成立狀態投影，新關係仍可登記，withdrawn／待覆核不得被舊 plan 復活。

圖查詢只在現值恰為舊系統預設 `path:concepts` 時，於已審閱交易中升成 §1 的預設；自訂 query 及其餘原生設定保留。before／after、CAS 與恢復依 Compiler §6，不能以修訂名義作全庫 prune。

語義圖合格須有可重跑連結檢查、證據型 bridge、整理隊列、候選接合和可查證提取；缺口是維護輸入，不能用畫面好看或工具欄位齊全掩蓋。
