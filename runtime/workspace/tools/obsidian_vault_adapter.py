"""Write one verified URL slice into the local Obsidian vault.

This is the first small Obsidian-first adapter slice. It intentionally handles
one reviewed Markdown slice at a time and verifies the files it writes.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

try:
    from tools.workspace_paths import WorkspacePathError, managed_vault_path, vault_path, vault_relative_path
except ModuleNotFoundError:
    from workspace_paths import WorkspacePathError, managed_vault_path, vault_path, vault_relative_path  # type: ignore

try:
    from tools.reader_surface_guard import (
        has_personal_layer,
        next_level_two_heading_offset,
        personal_layer_heading_match,
    )
except ModuleNotFoundError:
    from reader_surface_guard import (  # type: ignore
        has_personal_layer,
        next_level_two_heading_offset,
        personal_layer_heading_match,
    )

try:
    from tools.management_exclusion import assert_management_idle_for_vault
except ModuleNotFoundError:
    from management_exclusion import assert_management_idle_for_vault  # type: ignore

try:
    from tools.image_source_contract import (
        ImageSourceContractError,
        canonical_analysis_sha256,
        inspect_image,
        validate_visual_analysis,
    )
    from tools.source_identity import normalize_url
except ModuleNotFoundError:
    from image_source_contract import (
        ImageSourceContractError,
        canonical_analysis_sha256,
        inspect_image,
        validate_visual_analysis,
    )
    from source_identity import normalize_url

try:
    from tools.infographic_compiler import canonical_infographic_asset_filename
except ModuleNotFoundError:
    from infographic_compiler import canonical_infographic_asset_filename  # type: ignore

try:
    from tools.infographic_prompt_contract import infographic_spec_read_receipt_text
except ModuleNotFoundError:
    from infographic_prompt_contract import infographic_spec_read_receipt_text  # type: ignore

try:
    from tools.case_evidence_contract import (
        ASSET_MODE_CASE,
        ASSET_MODE_FORMAL,
        ASSET_MODE_KNOWLEDGE,
        CaseEvidenceError,
        case_asset_paths as contract_case_asset_paths,
        package_manifest as case_package_manifest,
    )
except ModuleNotFoundError:
    from case_evidence_contract import (  # type: ignore
        ASSET_MODE_CASE,
        ASSET_MODE_FORMAL,
        ASSET_MODE_KNOWLEDGE,
        CaseEvidenceError,
        case_asset_paths as contract_case_asset_paths,
        package_manifest as case_package_manifest,
    )

try:
    from tools.graph_preset import ensure_initial_graph_preset, graph_settings_status, reviewed_active_concept_filter
except ModuleNotFoundError:
    from graph_preset import ensure_initial_graph_preset, graph_settings_status, reviewed_active_concept_filter  # type: ignore

try:
    from tools.youtube_transcript_capture import (
        TRANSCRIPT_ARCHIVE_MODE,
        caption_fragments_grounded,
        caption_reading_chunks,
        has_cue_timing_line,
        is_youtube_caption_transcript,
    )
except ModuleNotFoundError:
    from youtube_transcript_capture import (  # type: ignore
        TRANSCRIPT_ARCHIVE_MODE,
        caption_fragments_grounded,
        caption_reading_chunks,
        has_cue_timing_line,
        is_youtube_caption_transcript,
    )


DEFAULT_SLICE = Path("inputs/source.md")
DEFAULT_VAULT = vault_relative_path()
REPO_ROOT = Path(__file__).resolve().parents[1]
INFOGRAPHIC_SPEC_PATH = REPO_ROOT / "docs" / "資訊圖生成規格.md"
INDEX_FIELD_MARKERS = (
    "\n## Obsidian-ready 索引欄位",
    "\n## Notion-ready 索引欄位",
)
BLOCKING_READABILITY_STATUSES = {"partial_capture", "source_blocked", "invalidated", "user_text_pending_review", "user_image_pending_review"}
VALID_SOURCE_TYPES = {"Facebook", "Twitter/X", "公司官網", "新聞", "部落格", "影片", "官方文件", "使用者提供文字", "使用者提供圖片", "其他"}
VALID_SOURCE_INPUT_KINDS = {"url", "user_provided_text", "user_provided_image"}
VALID_SOURCE_RETENTION_MODES = {"summarized_allowed", "full_body_required", "original_image_required", TRANSCRIPT_ARCHIVE_MODE}
# Modes that record the accepted body hash.  The archive mode keeps the body in
# the package, so its hash points at the archive, not at a Vault section.
CANONICAL_HASH_RETENTION_MODES = {"full_body_required", TRANSCRIPT_ARCHIVE_MODE}
VALID_KNOWLEDGE_USE = {"事實真源", "工作流程", "學習材料", "判斷材料", "品味材料", "行動材料", "噪音暫存"}
DEFAULT_AI_ADOPTION_STATUS = "暫存"
DEFAULT_AI_ADOPTION_REASON = "AI 尚未形成採納判斷。"
DEFAULT_TASTE_NETWORK_REASON = "來源主體是工作、學習或技術材料，暫不進入品味探索網。"
DEFAULT_PERSONALIZATION_STATUS = "personalization_blocked"
DEFAULT_TASTE_LINK_STATUS = "not_applicable"
DEFAULT_MANUAL_CORRECTION_STATUS = "無修正"
DEFAULT_MANUAL_CORRECTION_ACTION = "不同意 AI 判斷時才改 status"
VALID_COMPOUNDING_STATUSES = {"has_insight", "no_compounding_insight", "needs_context"}
VALID_COMPOUNDING_TYPES = ("補底", "加深", "連接", "修正", "行動")
DEFAULT_COMPOUNDING_STATUS = "needs_context"
DEFAULT_COMPOUNDING_NEXT_ACTION = "先把這篇當成背景材料保留；有更多同類內容時，再判斷它是否形成新洞見。"
DEFAULT_COMPOUNDING_CONTEXT_JUDGMENTS = {
    label: "待 AI 判斷；需比對實際既有來源、知識點或項目錨點。"
    for label in VALID_COMPOUNDING_TYPES
}
DEFAULT_HUMAN_VERIFICATION_ITEMS = [
    "只有不同意 AI 判斷時才手動修正 status",
    "修正時直接更新 adoption_status / taste_link_status / personalization_status / compounding_insight_status",
]
REQUIRED_LEARNING_CHECK_TYPES = ("理解題", "保持題", "遷移題", "應用任務")
APPLICATION_DRAFT_PLACEHOLDERS = ("待 AI 補寫", "待 AI 判斷", "待補", "待判斷", "pending")
APPLICATION_NON_LINK_PLACEHOLDERS = ("暫未建立", "未建立", "未有", "不適用", "無既有知識扣連")
APPLICATION_READER_RESIDUE = (
    "補完跨來源洞見判斷後再正式採納",
    "舊資料只完成機制欄位補齊",
    "未作重新判斷",
)
MACHINE_ID_WIKILINK_RE = re.compile(r"\[\[((?:KP-|KS-|APP-)[^\]|]+)(?:\|[^\]]+)?\]\]")
CONCEPT_REVIEW_PHRASES = (
    "是否值得留下",
    "是否值得採納",
    "是否值得收藏",
    "是否應該留下",
    "是否需要保留",
)
CONCEPT_TITLE_BAD_ENDINGS = (
    "不",
    "只",
    "時",
    "中",
    "以",
    "由",
    "把",
    "與",
    "或",
    "可",
    "需要",
    "涉及",
)
CONCEPT_TITLE_BAD_ENGLISH_ENDINGS = ("provide", "plan", "clean work")
CONCEPT_TITLE_EXACT_OVERRIDES = {
    "Agent 適合承擔問題已大致定義的 clean work；涉及真正問題、反對、限制、風險和取捨的 messy work，仍需要人創造 context 並承擔問責": "Agent clean work 與 messy work 邊界",
    "AI 應先用來放大團隊野心，而不是把縮編當成第一反應": "AI 放大團隊野心",
    "AI 採用成效不應只用使用量衡量": "AI 採用成效衡量",
    "AI agent 的核心轉折不是回答更像人，而是能跨工具、跨環境把任務端到端完成": "Agent 由對話回答轉向端到端執行",
    "agent 起步時需要更清楚的委派與驗證，所以短查詢未必值得；多步工作較能受惠於較低每步執行成本": "多步任務更能攤薄 agent 委派成本",
    "評估 agent 不應只看使用量，而要看它有沒有增加自主執行、降低不滿、節省時間成本，並擴張可完成的工作範圍": "AI 採用成效衡量",
    "agent 把人從逐步執行每個動作，推向定義目標、監督輸出、驗證結果與延伸任務": "人的角色由操作者轉向監督者",
    "agent 不只令原有工作更快，也讓使用者跨職業邊界、跨知識領域，嘗試更高認知與更複合的工作": "Agent 擴張可完成工作的範圍",
    "這篇研究的數字可作 agent 採用判斷材料，但必須保留早期採用、單一產品生態、估算方法和 LLM 分類誤差等限制": "Agent 採用證據需要外推限制",
    "Artifact 不是 application；沒有後端、不能保存表單、不能多路由": "Artifact 適用邊界",
    "CLAUDE.md 宜輕量，細節可放入 Skills 或可按需載入的參考檔": "Claude 指令檔輕量化",
    "MCP 是把 AI 應用連接到外部系統的開放標準": "MCP 外部系統連接",
    "Prompt 只是 Claude context 的一部分；系統提示、Skills、CLAUDE.md、memory 與工具結果都會影響輸出": "Claude context 組成",
    "Properties 適合短小、原子、可供人與機器閱讀的資料，不支援 Markdown 渲染": "Properties Markdown 限制",
    "Skill 變更不應只靠人工觀感；至少要改善既有結果或加入能捕捉新失敗的測試覆蓋": "Skill 變更驗收",
    "Sonnet 5 預設 adaptive thinking；manual extended thinking 已移除": "Sonnet 5 adaptive thinking",
    "一項 29 人廣泛性焦慮症大學生研究中，平均 91.4% 的可驗證擔憂沒有成真；不可外推為所有人的普遍比例": "可驗證擔憂未成真率",
    "品味可由記錄、整理、回饋、再使用轉化為外部持久化個人資產": "品味持久化資產",
    "外部研究支持巨石陣不是孤立物件，而是處於大型考古景觀之中": "巨石陣考古景觀",
    "學習成效的核心動作不是把資料塞入腦袋，而是在看不見答案時把知識提取出來": "學習成效靠提取練習",
    "寫 poster prompt 時，明確指定 style、mood、color palette、layout，再用 variations 與 refinement 迭代，比一次寫出複雜長 prompt 更可靠": "poster prompt 規格與迭代",
    "對這類 AI 考古敘事，入庫時應把來源主張、研究背景與推論跳躍拆開保存": "AI 考古敘事入庫拆分",
    "工作面應按任務重量選擇：快速問答用 Chat，多來源與大型交付用 Work，程式碼和開發工具用 Codex": "工作面任務重量選擇",
    "工具擴展的重點不是連接越多工具越好，而是能選擇工具、讀懂回傳結果並處理工具失敗": "工具擴展與失敗處理",
    "平台應收斂到一至兩個核心主軸,避免百花齊放造成管理混亂": "平台核心主軸收斂",
    "念頭是大腦對事件的解讀，不應在未核實前直接當成事實": "念頭不是事實",
    "提示是反覆合作：先看首次結果，再用後續訊息補來源、修方向或調細節，不必每次重啟": "提示反覆合作",
    "提示詞反覆失敗可源於未明說或未察覺的條件，而不只是不會措辭": "提示詞失敗的隱含條件",
    "新發布需要權限確認；同一 artifact 更新會沿用 URL，且每次發布形成版本": "Artifact 發布版本邊界",
    "是否可發布 artifact 受 plan、登入、provider、組織政策、surface 版本與停用設定共同限制": "Artifact 發布限制",
    "管理者應用工作原型找缺口，而不是急著取消既有職稱": "管理者用原型找缺口",
    "邊界應保護不可改動、不可猜測或不可未經核准執行的事項，而非微管理全部步驟": "邊界保護範圍",
    "部分高價值知識不是等待搜尋的既有資料，而是在組織互動、衝突、解釋與決策中才形成": "高價值知識形成",
    "開發時應先找最簡單方案,只有在有需要時才增加複雜度": "開發複雜度控制",
    "隔日重看並刪掉不會改變行動的句子，可區分真正吸收與暫存": "隔日重看刪除暫存句子",
}
REQUIRED_PNG_GENERATION_METHOD = "gpt-image-2 直接生成 PNG（經合規背景 worker）"
PROVIDED_PNG_RETENTION_METHOD = "使用者提供 PNG 原圖保留"
PNG_GENERATION_ALLOWED_MARKERS = ("gpt-image-2", "直接生成", "png")
PROVIDED_PNG_ALLOWED_MARKERS = ("使用者提供", "png", "原圖", "保留")
PNG_GENERATION_FORBIDDEN_MARKERS = (
    "svg",
    "html",
    "canvas",
    "screenshot",
    "browser screenshot",
    "截圖",
    "openai api",
    "openai sdk",
    "外部腳本",
    "script",
    "本地生成",
    "local",
    "轉檔",
    "轉成",
    "流程驗收圖",
    "不是最終視覺風格樣板",
    "偽",
)
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


@dataclass(frozen=True)
class KnowledgePoint:
    kp_id: str
    text: str
    kind: str
    confidence: str
    evidence: str
    title: str = ""
    reusable_concept_status: str = "needs_review"
    reusable_concept_name: str = ""
    reusable_concept_reason: str = ""
    understanding_delta: str = ""


@dataclass(frozen=True)
class KnowledgeRelation:
    source_kp_id: str
    target_kp_id: str
    relation: str
    evidence: str
    confidence: str


@dataclass(frozen=True)
class CrossSourceLink:
    """A plan-validated relation from a KP of this source to a linked KP of an older source."""
    source_kp_id: str
    target_kp_uid: str
    relation: str
    insight: str
    evidence: str
    confidence: str


@dataclass(frozen=True)
class SliceData:
    source_id: str
    slug: str
    title: str
    source_url: str
    source_input_kind: str
    declared_source_identity: str
    source_content_sha256: str
    source_image_sha256: str
    source_image_path: Path | None
    source_image_mime: str
    source_image_width: int
    source_image_height: int
    source_retention_mode: str
    canonical_body_sha256: str
    canonical_body_markdown: str
    canonical_body_representative_anchors: tuple[str, ...]
    source_url_binding_status: str
    source_type: str
    capture_date: str
    readability_status: str
    ai_source_analysis_status: str
    ai_source_analysis_evidence: str
    summary: str
    knowledge_use: list[str]
    taste_network_status: str
    taste_network_reason: str
    source_confidence: str
    retrieval_aliases: list[str]
    source_markdown: str
    application_markdown: str
    asset_mode: str
    infographic_status: str
    asset_path: Path | None
    asset_paths: tuple[Path, ...]
    case_evidence_manifest_path: Path | None
    case_evidence_manifest_sha256: str
    case_evidence_asset_paths: tuple[Path, ...]
    case_evidence_image_paths: tuple[Path, ...]
    infographic_blocks: tuple[str, ...]
    png_generation_method: str
    png_semantic_validation_status: str
    png_semantic_validation_evidence: str
    png_reverse_review_status: str
    png_reverse_review_evidence: str
    infographic_bridge_logic: str
    infographic_connection_reasons: str
    infographic_bridge_validation: str
    infographic_importance_coverage: str
    infographic_count_decision: str
    coverage_audit_status: str
    coverage_unit_total: int
    coverage_unit_infographic_mapped: int
    coverage_unit_omitted: int
    coverage_unit_summary_only: int
    infographic_page_count: int
    infographic_multi_volume_status: str
    knowledge_points: list[KnowledgePoint]
    relations: list[KnowledgeRelation]
    # Present only when the slice carries a prior_knowledge_json projection (current compact plans).
    cross_source_links: tuple[CrossSourceLink, ...] = ()
    prior_knowledge_applies: bool = False
    prior_job_id: str = ""


@dataclass(frozen=True)
class SourceRegistryResolution:
    source_identity: str
    source_note: Path
    mode: str
    issue: str | None = None


class AdapterStop(Exception):
    def __init__(self, status: str, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def main() -> int:
    parser = argparse.ArgumentParser(description="Write a URL slice into the Obsidian vault.")
    parser.add_argument("--source-slice", type=Path, default=DEFAULT_SLICE)
    parser.add_argument("--vault", type=Path, default=DEFAULT_VAULT)
    parser.add_argument("--max-kp", type=int, default=3)
    parser.add_argument("--write", action="store_true", help="Write files. Without this, only plan.")
    parser.add_argument("--verify", action="store_true", help="Verify existing or newly written files.")
    parser.add_argument(
        "--source-local",
        action="store_true",
        help="Write and read back only the current source artifacts; skip unrelated whole-vault maintenance.",
    )
    parser.add_argument("--vault-health", action="store_true", help="Check whole-vault structural invariants.")
    parser.add_argument("--duplicate-review", action="store_true", help="List knowledge-point duplicate candidates.")
    parser.add_argument("--resolve-duplicate", help="Resolve one duplicate candidate by kp_uid.")
    parser.add_argument("--duplicate-decision", choices=("keep", "merge"), help="Resolution decision for --resolve-duplicate.")
    parser.add_argument("--duplicate-target", help="Canonical kp_uid for a merge decision. Defaults to existing duplicate_of.")
    parser.add_argument("--duplicate-reason", default="", help="Short human review reason for a duplicate decision.")
    parser.add_argument("--reviewer", default="agent", help="Reviewer label stored in duplicate review metadata.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable output.")
    args = parser.parse_args()

    try:
        return run(args)
    except AdapterStop as exc:
        result = failure_result(args, exc)
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print_human(result)
        return 1


def run(args: argparse.Namespace) -> int:
    root = Path.cwd().resolve()
    vault = resolve_inside(root, args.vault)
    if args.vault_health:
        result = vault_health_report(root, vault)
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print_human(result)
        return 0 if result["status"] == "passed" else 1
    if args.duplicate_review:
        result = duplicate_review_report(root, vault)
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print_human(result)
        return 0 if result["status"] == "ready" else 1
    if args.resolve_duplicate:
        result = resolve_duplicate_review(
            root,
            vault,
            args.resolve_duplicate,
            args.duplicate_decision,
            args.duplicate_target,
            args.duplicate_reason,
            args.reviewer,
            args.write,
        )
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print_human(result)
        return 0 if result["status"] == "ready" else 1

    source_slice = resolve_inside(root, args.source_slice)
    data = parse_slice(source_slice, args.max_kp)
    plan = build_plan(root, vault, source_slice, data)
    preflight = preflight_report(plan)

    verify_only = args.verify and not args.write
    result = render_result(root, args.write, plan, preflight)

    if args.write and preflight["status"] != "passed":
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print_human(result)
        return 1

    if args.write:
        write_plan(plan, source_local=args.source_local)

    result = render_result(root, args.write, plan, preflight)

    if args.verify or args.write:
        result["verification"] = verify_plan(plan, source_local=args.source_local)
        if verify_only:
            verification = result["verification"]
            assert isinstance(verification, dict)
            result["status"] = "ready" if verification["passed"] else "needs_review"
            result["write_blocked"] = False

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print_human(result)
    if verify_only:
        verification = result["verification"]
        assert isinstance(verification, dict)
        return 0 if verification["passed"] else 1
    return 0 if preflight["status"] == "passed" else 1


def failure_result(args: argparse.Namespace, exc: AdapterStop) -> dict[str, object]:
    return {
        "mode": "write" if args.write else "dry_run",
        "status": exc.status,
        "write_blocked": True,
        "issues": [
            {
                "status": exc.status,
                "code": "adapter_input_error",
                "message": exc.message,
            }
        ],
    }


def render_result(
    root: Path,
    wrote: bool,
    plan: dict[str, object],
    preflight: dict[str, object],
) -> dict[str, object]:
    status = "ready" if preflight["status"] == "passed" else str(preflight["status"])
    return {
        "mode": "write" if wrote else "dry_run",
        "status": status,
        "write_blocked": preflight["status"] != "passed",
        "registry": str(plan["registry_file"].relative_to(root)),
        "knowledge_point_registry": str(plan["knowledge_point_registry_file"].relative_to(root)),
        "relation_registry": str(plan["relation_registry_file"].relative_to(root)),
        "source_identity": plan["source_identity"],
        "registry_resolution": plan["registry_resolution"],
        "registry_issue": plan["registry_issue"],
        "source_note": str(plan["source_note"].relative_to(root)),
        "application_note": str(plan["application_note"].relative_to(root)),
        "knowledge_points": [str(path.relative_to(root)) for path in plan["knowledge_point_notes"]],
        "concepts": [
            str(path.relative_to(root))
            for path in (
                planned_concept_note_path(plan["vault"], kp, note)
                for kp, note in zip(plan["data"].knowledge_points, plan["knowledge_point_notes"])
            )
            if path is not None
        ],
        "asset": str(plan["asset_dest"].relative_to(root)) if plan["asset_dest"] else None,
        "assets": [
            str(path.relative_to(root))
            for path in [*plan["asset_dests"], *plan.get("case_asset_dests", [])]
            if isinstance(path, Path)
        ],
        "bases": [str(path.relative_to(root)) for path in plan["base_files"]],
        "updated_existing": plan["updated_existing"],
        "preflight": preflight,
    }


def resolve_inside(root: Path, target: Path) -> Path:
    resolved = (root / target).resolve() if not target.is_absolute() else target.resolve()
    if root != resolved and root not in resolved.parents:
        raise SystemExit(f"Refusing path outside workspace: {target}")
    return resolved


def parse_slice(source_slice: Path, max_kp: int, *, markdown: str | None = None) -> SliceData:
    text = source_slice.read_text(encoding="utf-8-sig") if markdown is None else markdown
    source_id = source_slice.stem.split("_", 1)[0]
    slug = source_slice.stem.split("_", 1)[1] if "_" in source_slice.stem else source_slice.stem
    title = first_heading(text).replace(" — ", " - ")
    audit = parse_table_after_heading(text, "## 來源稽核")
    source_input_kind = (audit.get("來源輸入類型", "url") or "url").strip().lower()
    tldr = parse_numbered_list_after_heading(text, "## TL;DR")
    routing = parse_table_after_heading(text, "## 知識用途分流")
    kp_rows = parse_kp_rows(text)[:max_kp]
    relations = parse_relation_rows(text)
    asset_mode = (audit.get("資產模式") or ASSET_MODE_FORMAL).strip()
    if asset_mode not in {ASSET_MODE_FORMAL, ASSET_MODE_CASE, ASSET_MODE_KNOWLEDGE}:
        raise AdapterStop("blocked", "source slice has an unsupported asset mode")
    infographic_blocks = extract_infographic_blocks(text)
    coverage_audit = parse_table_after_heading(text, "## 覆蓋審計")
    # The imagegen template may put the Markdown image immediately before its
    # `### 資訊圖` heading, so scope to the enclosing section rather than block.
    asset_paths = find_images(source_slice.parent, section_after(text, "## 資訊圖")) if asset_mode == ASSET_MODE_FORMAL else []
    asset_path = asset_paths[0] if asset_paths else None
    infographic = infographic_blocks[0] if infographic_blocks else ""
    canonical_body_markdown = extract_canonical_body_markdown(text)
    # User-provided images have no canonical text body.  Their IMG-* evidence
    # is checked only after the original bytes and formal visual analysis have
    # been verified in user_image_provenance_issues().
    grounding_body = canonical_body_markdown
    if (audit.get("正文保存模式") or "").strip() == TRANSCRIPT_ARCHIVE_MODE:
        # The source note holds no transcript; quotations are grounded in the
        # package archive that sits beside the slice.
        archive_body = source_slice.parent / "canonical_source.md"
        if not archive_body.is_file():
            raise AdapterStop("blocked", "transcript archive canonical_source.md is missing beside the source slice")
        grounding_body = archive_body.read_text(encoding="utf-8-sig").rstrip("\n")
    cross_source_links, prior_knowledge_applies, prior_job_id = parse_prior_knowledge(text)
    if source_input_kind != "user_provided_image":
        validate_source_anchored_knowledge(kp_rows, relations, grounding_body)
        validate_source_anchored_knowledge(
            [],
            [KnowledgeRelation(link.source_kp_id, link.target_kp_uid, link.relation, link.evidence, link.confidence)
             for link in cross_source_links],
            grounding_body,
        )
    source_image_path = slice_local_asset_path(source_slice.parent, audit.get("原始圖片檔", ""))
    case_evidence_manifest_path: Path | None = None
    case_evidence_manifest_sha256 = ""
    case_evidence_asset_paths: tuple[Path, ...] = ()
    case_evidence_image_paths: tuple[Path, ...] = ()
    if asset_mode == ASSET_MODE_CASE:
        case_evidence_manifest_path = slice_local_asset_path(source_slice.parent, audit.get("Evidence manifest", ""))
        case_evidence_manifest_sha256 = audit.get("Manifest SHA-256", "")
        if not case_evidence_manifest_path or not case_evidence_manifest_sha256:
            raise AdapterStop("blocked", "case evidence source slice is missing its immutable manifest binding")
        try:
            case_manifest = case_package_manifest(source_slice.parent, {
                "case_evidence_manifest_path": case_evidence_manifest_path.relative_to(source_slice.parent).as_posix(),
                "case_evidence_manifest_sha256": case_evidence_manifest_sha256,
                "case_evidence_source_manifest_sha256": audit.get("Source manifest SHA-256", ""),
                "source_url": audit.get("URL") or audit.get("來源 URL") or audit.get("來源URL") or "",
            })
        except (CaseEvidenceError, ValueError) as exc:
            raise AdapterStop("blocked", f"case evidence manifest cannot be revalidated: {exc}") from exc
        case_evidence_asset_paths = tuple([case_evidence_manifest_path, *contract_case_asset_paths(source_slice.parent, case_manifest)])
        image_paths = [
            slice_local_asset_path(source_slice.parent, row.get("path", ""))
            for row in case_manifest.get("screenshots", [])
            if isinstance(row, dict)
        ]
        image_paths.append(slice_local_asset_path(source_slice.parent, case_manifest["reproduction"].get("comparison_screenshot_path", "")))
        case_evidence_image_paths = tuple(path for path in image_paths if path is not None)

    retrieval_aliases = parse_retrieval_aliases(text)
    source_markdown = prepare_source_markdown(remove_retrieval_aliases_transport_comment(text), source_id)

    return SliceData(
        source_id=source_id,
        slug=slug,
        title=audit.get("來源名稱") or title,
        source_url=audit.get("URL") or audit.get("來源 URL") or audit.get("來源URL") or "",
        source_input_kind=source_input_kind,
        declared_source_identity=audit.get("來源身分", ""),
        source_content_sha256=audit.get("來源內容 SHA-256", ""),
        source_image_sha256=audit.get("來源圖片 SHA-256") or audit.get("原始圖片 SHA-256", ""),
        source_image_path=source_image_path,
        source_image_mime=audit.get("圖片 MIME") or audit.get("原始圖片 MIME", ""),
        source_image_width=parse_positive_int((audit.get("圖片尺寸", "").split("×", 1)[0] or audit.get("原始圖片寬度", "0"))),
        source_image_height=parse_positive_int((audit.get("圖片尺寸", "").split("×", 1)[1] if "×" in audit.get("圖片尺寸", "") else audit.get("原始圖片高度", "0"))),
        source_retention_mode=(audit.get("正文保存模式", "summarized_allowed") or "summarized_allowed").strip(),
        canonical_body_sha256=audit.get("正文保存 SHA-256", ""),
        canonical_body_markdown=canonical_body_markdown,
        canonical_body_representative_anchors=tuple(canonical_body_representative_anchors(text, audit)),
        source_url_binding_status=audit.get("URL 綁定狀態", "legacy"),
        source_type=audit.get("來源類型", "其他"),
        capture_date=audit.get("擷取日期", str(date.today())),
        readability_status=audit.get("可讀性狀態", "needs_review"),
        ai_source_analysis_status=analysis_status(audit),
        ai_source_analysis_evidence=analysis_evidence(audit),
        summary=short_property_summary(tldr),
        knowledge_use=split_knowledge_use(routing.get("主要用途", "")),
        taste_network_status=routing.get("是否入品味探索網", "需更多脈絡"),
        taste_network_reason=(
            routing.get("不入或入品味網原因")
            or routing.get("不入或入網原因")
            or routing.get("不入品味網原因")
            or routing.get("入品味網原因")
            or DEFAULT_TASTE_NETWORK_REASON
        ),
        source_confidence="高" if "官方" in audit.get("來源類型", "") else "中",
        retrieval_aliases=retrieval_aliases,
        source_markdown=source_markdown,
        application_markdown=extract_application_markdown(text),
        asset_mode=asset_mode,
        infographic_status=(
            audit.get("資訊圖狀態", "not_requested")
            if asset_mode == ASSET_MODE_KNOWLEDGE
            else "completed"
        ),
        asset_path=asset_path,
        asset_paths=tuple(asset_paths),
        case_evidence_manifest_path=case_evidence_manifest_path,
        case_evidence_manifest_sha256=case_evidence_manifest_sha256,
        case_evidence_asset_paths=case_evidence_asset_paths,
        case_evidence_image_paths=case_evidence_image_paths,
        infographic_blocks=tuple(infographic_blocks),
        png_generation_method=infographic_long_field(
            infographic,
            (
                "PNG 生成方式",
                "PNG generation method",
                "png_generation_method",
            ),
            default="missing",
        ),
        png_semantic_validation_status=png_semantic_status(infographic),
        png_semantic_validation_evidence=png_semantic_evidence(infographic),
        png_reverse_review_status=png_reverse_review_status(infographic),
        png_reverse_review_evidence=png_reverse_review_evidence(infographic),
        infographic_bridge_logic=infographic_long_field(
            infographic,
            ("承接邏輯", "bridge_logic"),
            default="missing",
        ),
        infographic_connection_reasons=infographic_long_field(
            infographic,
            ("連線 / 分組理由", "connection_reasons"),
            default="missing",
        ),
        infographic_bridge_validation=infographic_long_field(
            infographic,
            ("斷橋檢查", "bridge_validation"),
            default="missing",
        ),
        infographic_importance_coverage=infographic_long_field(
            infographic,
            ("重要性覆蓋", "importance_coverage"),
            default="missing",
        ),
        infographic_count_decision=infographic_long_field(
            infographic,
            ("本 URL 資訊圖數量判斷", "資訊圖數量判斷", "image_count_decision"),
            default="missing",
        ),
        coverage_audit_status=coverage_audit.get("覆蓋狀態", "missing"),
        coverage_unit_total=parse_positive_int(coverage_audit.get("重要單元總數", "0")),
        coverage_unit_infographic_mapped=parse_positive_int(coverage_audit.get("已映射入資訊圖", "0")),
        coverage_unit_omitted=parse_positive_int(coverage_audit.get("明確省略項", "0")),
        coverage_unit_summary_only=parse_positive_int(coverage_audit.get("只作摘要項", "0")),
        infographic_page_count=parse_positive_int(coverage_audit.get("正式資訊圖頁數", "0")),
        infographic_multi_volume_status=coverage_audit.get("多卷狀態", "missing"),
        knowledge_points=kp_rows,
        relations=relations,
        cross_source_links=cross_source_links,
        prior_knowledge_applies=prior_knowledge_applies,
        prior_job_id=prior_job_id,
    )


def first_heading(text: str) -> str:
    for line in text.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return "Untitled Source"


def parse_table_after_heading(text: str, heading: str) -> dict[str, str]:
    block = section_after(text, heading)
    rows: dict[str, str] = {}
    for line in block.splitlines():
        if not line.startswith("|") or is_markdown_table_divider(line):
            continue
        cells = [cell.strip() for cell in split_markdown_table_cells(line)]
        if len(cells) >= 2 and cells[0] not in {"欄位", "類型"}:
            rows[clean_table_cell(cells[0])] = clean_table_cell(cells[1])
    return rows


RETRIEVAL_ALIASES_TRANSPORT_RE = re.compile(
    r"(?m)^<!-- retrieval_aliases_json: (?P<aliases>.*?) -->\r?\n(?:\r?\n)?\Z"
)
ORIGINAL_LAYER_HEADING_RE = re.compile(r"(?m)^## 原文層[ \t]*\r?$")


def retrieval_aliases_transport_match(text: str) -> re.Match[str] | None:
    """Return only the compiler annotation immediately before the original layer."""
    original_layer = ORIGINAL_LAYER_HEADING_RE.search(text)
    if original_layer is None:
        return None
    return RETRIEVAL_ALIASES_TRANSPORT_RE.search(text[:original_layer.start()])


def parse_retrieval_aliases(text: str) -> list[str]:
    """Read the compiler-only alias projection without making it reader-visible."""
    match = retrieval_aliases_transport_match(text)
    if not match:
        return []
    try:
        aliases = json.loads(match.group("aliases"))
    except json.JSONDecodeError:
        return []
    if not isinstance(aliases, list) or not all(isinstance(item, str) for item in aliases):
        return []
    return [item.strip() for item in aliases if item.strip()]


def remove_retrieval_aliases_transport_comment(text: str) -> str:
    """Drop the compiler transport line and its following blank line from output."""
    match = retrieval_aliases_transport_match(text)
    return text[:match.start()] + text[match.end():] if match else text


def split_markdown_table_cells(line: str) -> list[str]:
    """Split a Markdown table row without treating an escaped pipe as a cell break."""
    cells: list[str] = []
    current: list[str] = []
    escaped = False
    for char in line.strip().strip("|"):
        if char == "|" and not escaped:
            cells.append("".join(current))
            current = []
            continue
        current.append(char)
        if char == "\\":
            escaped = not escaped
        else:
            escaped = False
    cells.append("".join(current))
    return cells


def is_markdown_table_divider(line: str) -> bool:
    """Recognize only a complete Markdown divider row, never cell content."""

    if not line.startswith("|"):
        return False
    cells = [cell.strip() for cell in split_markdown_table_cells(line)]
    return bool(cells) and all(re.fullmatch(r":?-{3,}:?", cell) for cell in cells)


def clean_table_cell(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value.startswith("`") and value.endswith("`"):
        return value[1:-1].strip()
    return value.replace(r"\|", "|")


def parse_positive_int(value: str) -> int:
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        return 0
    return parsed if parsed > 0 else 0


def slice_local_asset_path(slice_dir: Path, raw_path: str) -> Path | None:
    """Resolve a reviewed slice asset without accepting an original absolute path."""
    raw = raw_path.strip()
    if not raw:
        return None
    candidate_input = Path(raw)
    if candidate_input.is_absolute():
        return None
    slice_root = slice_dir.resolve()
    candidate = (slice_dir / candidate_input).resolve()
    return candidate if slice_root in candidate.parents and candidate.exists() else None


def required_audit_value(audit: dict[str, str], keys: tuple[str, ...], label: str) -> str:
    for key in keys:
        value = clean_table_cell(audit.get(key, ""))
        if value:
            return value
    raise AdapterStop("needs_review", f"missing {label} in source audit table")


def parse_numbered_list_after_heading(text: str, heading: str) -> list[str]:
    block = section_after(text, heading)
    items: list[str] = []
    for line in block.splitlines():
        match = re.match(r"\s*\d+\.\s+(.*)", line)
        if match:
            items.append(match.group(1).strip())
    return items


def short_property_summary(items: list[str], limit: int = 160) -> str:
    if not items:
        return ""
    summary = items[0].strip()
    if len(summary) <= limit:
        return summary
    return summary[: limit - 1].rstrip() + "…"


def section_after(text: str, heading: str) -> str:
    start = text.find(heading)
    if start == -1:
        return ""
    rest = text[start + len(heading) :]
    match = re.search(r"\n##\s+", rest)
    return rest[: match.start()] if match else rest


def extract_canonical_body_markdown(text: str) -> str:
    start = text.find("## 正文保存區")
    if start == -1:
        return ""
    block = text[start:]
    match = re.search(
        r"(?ms)^### Canonical article body\s*\n(?P<fence>`{3,})(?:markdown|md|text)?\s*\n(.*?)\n(?P=fence)",
        block,
    )
    return match.group(2).strip() if match else ""


def retained_original_location(mode: str | None) -> str:
    """Return the sole retained-original location for an accepted retention mode."""
    if mode == "full_body_required":
        return "source_note_body"
    if mode in {"original_image_required", "transcript_archive_required", "summarized_allowed", None, ""}:
        return "package_canonical"
    raise ValueError("unsupported source retention mode: " + str(mode))


def read_retained_original(mode: str | None, *, note_text: str | None = None, package: Path | None = None) -> str:
    """Read the immutable original from the location owned by ``mode``."""
    if retained_original_location(mode) == "source_note_body":
        if note_text is None:
            raise ValueError("retained source note text is required")
        body = extract_canonical_body_markdown(note_text)
        if not body:
            raise ValueError("retained source note lacks canonical body")
        return body
    if package is None:
        raise ValueError("retained source package is required")
    canonical = package / "canonical_source.md"
    if not canonical.is_file():
        raise ValueError("retained source package lacks canonical_source.md")
    text = canonical.read_text(encoding="utf-8")
    return text[:-1] if text.endswith("\n") else text


def retained_note_body_section(text: str) -> str:
    """Return the source-note retained-body section, preserving its exact slice."""
    # Original text is fenced and may itself contain H2 headings.
    collected: list[str] = []
    active = False
    fence = ""
    for line in text.splitlines():
        stripped = line.strip()
        if fence:
            if re.fullmatch(re.escape(fence[0]) + "{" + str(len(fence)) + r",}\s*", stripped):
                fence = ""
        else:
            match = re.match(r"^(`{3,}|~{3,})", stripped)
            if match:
                fence = match.group(1)
            elif re.match(r"^##\s+", line):
                if active:
                    break
                if re.fullmatch(r"##\s+正文保存區\s*", line):
                    active = True
                continue
        if active:
            collected.append(line)
    return "\n".join(collected).strip()


def retained_note_body_offsets(text: str) -> tuple[int, int] | None:
    """Return canonical body bounds for packet projection, or reject bad delimiters."""
    sections = list(re.finditer(r"(?m)^## 正文保存區[ \t]*(?=\n|\Z)", text))
    if not sections:
        return None
    starts = list(re.finditer(r"(?m)^### Canonical article body[ \t]*\n", text))
    ends = list(re.finditer(r"(?m)^## 知識用途分流[ \t]*(?=\n|\Z)", text))
    if len(sections) != 1 or len(starts) != 1 or len(ends) != 1:
        raise ValueError("canonical body is not delimited")
    if not (sections[0].start() < starts[0].start() < ends[0].start()):
        raise ValueError("canonical body is not delimited")
    return starts[0].end(), ends[0].start()


def canonical_body_markdown_present(text: str) -> bool:
    return "## 正文保存區" in text or bool(extract_canonical_body_markdown(text))


def canonical_body_fingerprint(markdown: str) -> str:
    return hashlib.sha256(markdown.strip().encode("utf-8")).hexdigest()


SOURCE_EVIDENCE_FRAGMENT_MARKER = "【來源片段】"
SOURCE_EVIDENCE_FRAGMENT_SEPARATOR = f"\n\n{SOURCE_EVIDENCE_FRAGMENT_MARKER}\n\n"


def canonical_body_contains_anchor(body: str, anchor: str) -> bool:
    """Match reviewed anchors across harmless source line wrapping.

    PDF-to-text extraction commonly wraps a sentence mid-phrase.  The source
    body remains byte- and hash-preserved; this readback check only collapses
    whitespace on both sides before testing whether the same anchor is present.
    A compiler-created multi-fragment anchor is valid only when every displayed
    source fragment is independently present; it never fabricates a continuous
    quotation from non-adjacent paragraphs.
    """

    normalized_body = re.sub(r"\s+", " ", body).strip()
    # Table rendering folds newlines to spaces, so retain the unique marker
    # while accepting the same compiler-created fragments after that roundtrip.
    fragments = anchor.split(SOURCE_EVIDENCE_FRAGMENT_MARKER)
    normalized_fragments = [re.sub(r"\s+", " ", fragment).strip() for fragment in fragments]
    if bool(normalized_fragments) and all(
        fragment and fragment in normalized_body for fragment in normalized_fragments
    ):
        return True
    # A YouTube caption archive keeps the cue lines in the package.  Its
    # program-built quotations are de-duplicated reading chunks (plus one time
    # link), so they are grounded by re-deriving the chunks from the archive.
    return caption_fragments_grounded(body, fragments) is True


def transcript_archive_declaration(text: str) -> dict[str, str]:
    """The ## 字幕存檔 table of a slice or source note (empty when absent)."""
    return parse_table_after_heading(text, "## 字幕存檔")


def transcript_archive_anchors(text: str) -> list[str]:
    """Reading-version anchors listed under ## 字幕存檔; only the list marker is removed."""
    block = markdown_subsection(section_after(text, "## 字幕存檔"), "閱讀版代表錨點")
    return [line.strip()[2:].strip() for line in block.splitlines() if line.strip().startswith("- ") and line.strip()[2:].strip()]


def canonical_body_representative_anchors(text: str, audit: dict[str, str]) -> list[str]:
    if (audit.get("正文保存模式") or "").strip() == TRANSCRIPT_ARCHIVE_MODE:
        return transcript_archive_anchors(text)
    anchors: list[str] = []
    block = markdown_subsection(section_after(text, "## 正文保存區"), "正文代表錨點")
    anchors.extend(concrete_list_items(block))
    inline = audit.get("正文代表錨點", "")
    # The reviewed list is authoritative. Splitting the display-only table
    # cell on " / " also splits formulas such as "1/do + 1/di / ...".
    if not anchors and inline and inline != "待補":
        anchors.extend([part.strip() for part in re.split(r"\s+/\s+|\s*；\s*|\s*;\s*", inline) if part.strip()])
    deduped: list[str] = []
    for anchor in anchors:
        normalized = anchor.strip()
        if normalized and normalized not in deduped and normalized != "待 AI 補代表錨點":
            deduped.append(normalized)
    return deduped


def parse_kp_rows(text: str) -> list[KnowledgePoint]:
    block = section_after(text, "### 知識點草案") or section_after(text, "## 原子知識點與關係")
    points: list[KnowledgePoint] = []
    match = re.search(r"^<!-- knowledge_semantics_json: (.+) -->$", text, re.M)
    semantics = json.loads(match[1]) if match else []
    if not isinstance(semantics, list) or any(not isinstance(row, dict) for row in semantics):
        raise AdapterStop("blocked", "invalid knowledge semantics projection")
    by_id = {row.get("kp_id"): row for row in semantics}
    if len(by_id) != len(semantics):
        raise AdapterStop("blocked", "duplicate knowledge semantics identity")
    for line in block.splitlines():
        if not line.startswith("|") or is_markdown_table_divider(line) or "知識點 ID" in line:
            continue
        cells = [clean_table_cell(cell) for cell in split_markdown_table_cells(line)]
        if cells and cells[0] in {"ID", "來源知識點"}:
            continue
        if cells and cells[0].startswith("KP-") and len(cells) != 5:
            raise AdapterStop("blocked", "knowledge-point row must contain exactly five cells")
        if len(cells) == 5 and cells[0].startswith("KP-") and not cells[1].startswith("KP-"):
            item = by_id.get(cells[0], {})
            concept = item.get("reusable_concept", {})
            if not isinstance(concept, dict) or concept.get("status", "needs_review") not in {"reusable", "not_reusable", "needs_review"}:
                raise AdapterStop("blocked", "invalid reusable concept decision")
            if concept.get("status") == "reusable" and (not concept.get("name") or not concept.get("reason")):
                raise AdapterStop("blocked", "reusable concept lacks analysed name/reason")
            points.append(KnowledgePoint(cells[0], cells[1], cells[2], cells[3], cells[4],
                str(item.get("title") or ""), str(concept.get("status") or "needs_review"),
                str(concept.get("name") or ""), str(concept.get("reason") or ""),
                str(concept.get("understanding_delta") or "")))
    if set(by_id) - {point.kp_id for point in points}:
        raise AdapterStop("blocked", "knowledge semantics references an unwritten point")
    return points


def validate_source_anchored_knowledge(
    points: list[KnowledgePoint],
    relations: list[KnowledgeRelation],
    canonical_body: str,
    *,
    verified_image_anchor_ids: set[str] | None = None,
) -> None:
    """Reject a write when a supposedly source-backed KP cannot be traced.

    This is intentionally a provenance gate, not an attempt to have Python
    invent semantic judgement. The compiler must provide a nonempty,
    non-placeholder excerpt that can be located in the canonical source;
    AI and human review still decide whether it fully supports the translated
    or transformed statement.
    """
    for label, anchor, claim in [
        *((f"knowledge point {point.kp_id}", point.evidence, point.text) for point in points),
        *((f"knowledge relation {relation.source_kp_id}->{relation.target_kp_id}", relation.evidence, relation.relation) for relation in relations),
    ]:
        normalized_anchor = re.sub(r"\s+", " ", anchor).strip()
        if not normalized_anchor or normalized_anchor in {"待補", "待 AI 補寫", "待 AI 判斷"}:
            raise AdapterStop("blocked", f"{label} requires a nonempty, non-placeholder source evidence anchor")
        if verified_image_anchor_ids is not None:
            # Image-analysis only requires an ID to start with IMG-.  Match
            # the already-verified, complete IDs instead of imposing a second
            # grammar here; longest first prevents IMG-001 matching a prefix
            # of a different ID.  Every remaining IMG- marker is rejected.
            image_evidence = anchor
            matched_image_anchor_spans: list[tuple[int, int]] = []
            for image_anchor_id in sorted(verified_image_anchor_ids, key=len, reverse=True):
                offset = 0
                while True:
                    start = image_evidence.find(image_anchor_id, offset)
                    if start < 0:
                        break
                    end = start + len(image_anchor_id)
                    offset = start + 1
                    overlaps_existing = any(start < existing_end and end > existing_start for existing_start, existing_end in matched_image_anchor_spans)
                    left_is_boundary = start == 0 or not (image_evidence[start - 1].isalnum() or image_evidence[start - 1] in "_-")
                    right_is_boundary = end == len(image_evidence) or not (image_evidence[end].isalnum() or image_evidence[end] in "_-")
                    if not overlaps_existing and left_is_boundary and right_is_boundary:
                        matched_image_anchor_spans.append((start, end))
            has_unverified_image_marker = any(
                not any(start <= marker.start() < end for start, end in matched_image_anchor_spans)
                for marker in re.finditer(r"IMG-", image_evidence)
            )
            if has_unverified_image_marker:
                raise AdapterStop("blocked", f"{label} evidence anchor references an unverified image anchor")
            if matched_image_anchor_spans:
                continue
        # Preserve explicit fragment boundaries so a compact-plan citation to
        # non-adjacent accepted source blocks is checked fragment-by-fragment,
        # never as a fabricated contiguous quotation.
        if not canonical_body_contains_anchor(canonical_body, anchor):
            raise AdapterStop("blocked", f"{label} evidence anchor: quote not in complete source")


def parse_prior_knowledge(text: str) -> tuple[tuple[CrossSourceLink, ...], bool, str]:
    """Cross-source links projected by the compiler beside the relation draft (absent for older slices)."""
    match = re.search(r"^<!-- prior_knowledge_json: (.+) -->$", text, re.M)
    if not match:
        return (), False, ""
    payload = json.loads(match[1])
    if not isinstance(payload, dict) or not isinstance(payload.get("cross_links"), list):
        raise AdapterStop("blocked", "invalid prior knowledge projection")
    links: list[CrossSourceLink] = []
    for item in payload["cross_links"]:
        if not isinstance(item, dict):
            raise AdapterStop("blocked", "invalid prior knowledge projection")
        link = CrossSourceLink(*(str(item.get(key) or "").strip() for key in (
            "source_kp_id", "target_kp_uid", "relation", "insight", "evidence", "confidence")))
        if not all((link.source_kp_id, link.target_kp_uid, link.relation, link.insight, link.evidence)):
            raise AdapterStop("blocked", "cross-source link requires KP, target, relation, insight and evidence")
        links.append(link)
    return tuple(links), True, str(payload.get("job_id") or "").strip()


def parse_relation_rows(text: str) -> list[KnowledgeRelation]:
    block = section_after(text, "### 關係草案") or section_after(text, "## 原子知識點與關係")
    relations: list[KnowledgeRelation] = []
    for line in block.splitlines():
        if not line.startswith("|") or is_markdown_table_divider(line) or "來源知識點" in line:
            continue
        cells = [clean_table_cell(cell) for cell in split_markdown_table_cells(line)]
        if cells and cells[0].startswith("KP-") and len(cells) != 5:
            raise AdapterStop("blocked", "knowledge-relation row must contain exactly five cells")
        if len(cells) == 5 and cells[0].startswith("KP-") and cells[1].startswith("KP-"):
            relations.append(KnowledgeRelation(cells[0], cells[1], cells[2], cells[3], cells[4]))
    return relations


def find_images(slice_dir: Path, text: str) -> list[Path]:
    slice_root = slice_dir.resolve()
    images: list[Path] = []
    seen: set[Path] = set()
    for match in re.finditer(r"!\[[^\]]*\]\(([^)]+)\)", text):
        raw_target = match.group(1).strip()
        if not raw_target or re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", raw_target):
            continue
        candidate = (slice_dir / raw_target).resolve()
        if candidate.exists() and slice_root in candidate.parents and candidate not in seen:
            images.append(candidate)
            seen.add(candidate)
    return images


def is_png_file(path: Path) -> bool:
    return png_file_issue(path) is None


def png_file_issue(path: Path) -> str | None:
    try:
        data = path.read_bytes()
    except OSError:
        return "missing_asset_file"
    if len(data) < 33 or not data.startswith(PNG_SIGNATURE) or data[12:16] != b"IHDR":
        return "invalid_png_asset"
    color_type = data[25]
    if color_type in {4, 6} or b"tRNS" in data:
        return "transparent_png_asset"
    return None


def extract_infographic_blocks(text: str) -> list[str]:
    section_match = re.search(r"\n## 資訊圖\n([\s\S]*?)(?=\n##\s+|\Z)", text)
    section = section_match.group(1) if section_match else text
    matches = re.finditer(r"### 資訊圖(?:\s+\d+)?:[\s\S]*?(?=\n### 資訊圖(?:\s+\d+)?:|\n##\s+|\Z)", section)
    return [match.group(0).strip() for match in matches]


def extract_infographic_block(text: str) -> str:
    blocks = extract_infographic_blocks(text)
    return blocks[0] if blocks else ""


def analysis_status(audit: dict[str, str]) -> str:
    return first_existing_value(
        audit,
        (
            "AI 來源分析狀態",
            "AI source analysis status",
            "source_analysis_status",
        ),
        default="missing",
    )


def analysis_evidence(audit: dict[str, str]) -> str:
    return first_existing_value(
        audit,
        (
            "AI 來源分析證據",
            "AI source analysis evidence",
            "source_analysis_evidence",
        ),
        default="",
    )


def png_semantic_status(block: str) -> str:
    return infographic_field(
        block,
        (
            "PNG 語義驗收狀態",
            "PNG semantic validation status",
            "png_semantic_validation_status",
        ),
        default="missing",
    )


def png_semantic_evidence(block: str) -> str:
    return infographic_field(
        block,
        (
            "PNG 語義驗收證據",
            "PNG semantic validation evidence",
            "png_semantic_validation_evidence",
            "語義驗收",
        ),
        default="",
    )


def png_reverse_review_status(block: str) -> str:
    return infographic_field(
        block,
        (
            "PNG 反審狀態",
            "PNG reverse review status",
            "png_reverse_review_status",
        ),
        default="missing",
    )


def png_reverse_review_evidence(block: str) -> str:
    return infographic_long_field(
        block,
        (
            "PNG 反審證據",
            "PNG reverse review evidence",
            "png_reverse_review_evidence",
        ),
        default="",
    )


def first_existing_value(mapping: dict[str, str], keys: tuple[str, ...], default: str = "") -> str:
    for key in keys:
        value = clean_table_cell(mapping.get(key, ""))
        if value:
            return value
    return default


def infographic_field(block: str, labels: tuple[str, ...], default: str = "") -> str:
    for label in labels:
        match = re.search(rf"^- {re.escape(label)}:(.*)$", block, flags=re.MULTILINE)
        if match:
            value = match.group(1).strip()
            if value:
                return value
    return default


def infographic_long_field(block: str, labels: tuple[str, ...], default: str = "") -> str:
    for label in labels:
        match = re.search(
            rf"^- {re.escape(label)}:(.*?)(?=^\- |\Z)",
            block,
            flags=re.MULTILINE | re.DOTALL,
        )
        if match:
            value = normalize_infographic_field_text(match.group(1))
            if value:
                return value
    return default


def normalize_infographic_field_text(value: str) -> str:
    lines = []
    for line in value.strip().splitlines():
        cleaned = line.strip()
        if cleaned.startswith("- "):
            cleaned = cleaned[2:].strip()
        if cleaned:
            lines.append(cleaned)
    return " ".join(lines).strip()


def incomplete_status(value: str, accepted: set[str]) -> bool:
    normalized = value.strip().casefold()
    if normalized in accepted:
        return False
    if not normalized:
        return True
    pending_markers = ("missing", "pending", "待", "待補", "待 ai", "未完成", "needs_review")
    return any(marker in normalized for marker in pending_markers) or normalized not in accepted


def incomplete_free_text(value: str) -> bool:
    normalized = value.strip().casefold()
    if not normalized:
        return True
    # 「待」是中文正常內容的一部分（例如「待辦」）。只把完整的
    # pending 值，或明確以待補／待 AI 開首的內容視作未完成，不能以
    # 任意子字串否決已寫好的讀者內容。
    pending_values = {"missing", "pending", "待", "待補", "待 ai", "未完成", "needs_review"}
    return normalized in pending_values or normalized.startswith(
        ("pending", "待補", "待 ai", "未完成", "needs_review")
    )


def png_generation_method_issue(value: str) -> str | None:
    normalized = value.strip().casefold()
    if incomplete_free_text(value):
        return (
            "PNG 生成方式缺失；正式資訊圖必須明確寫明由 gpt-image-2 經合規背景 worker 直接生成 PNG。"
        )
    forbidden = [marker for marker in PNG_GENERATION_FORBIDDEN_MARKERS if marker in normalized]
    if forbidden:
        return "PNG 生成方式包含禁用路線：" + ", ".join(forbidden)
    if all(marker in normalized for marker in PROVIDED_PNG_ALLOWED_MARKERS):
        return None
    missing = [marker for marker in PNG_GENERATION_ALLOWED_MARKERS if marker not in normalized]
    if missing:
        return "PNG 生成方式不完整；必須寫明：" + REQUIRED_PNG_GENERATION_METHOD + "，或：" + PROVIDED_PNG_RETENTION_METHOD
    return None


def provided_png_retention_method(value: str) -> bool:
    return png_generation_method_issue(value) is None and "使用者提供" in value


def png_reverse_review_evidence_issue(
    value: str,
    *,
    provided_original: bool = False,
) -> str | None:
    normalized = value.strip().casefold()
    pending_values = {"missing", "pending", "待", "待補", "待 ai", "未完成", "needs_review"}
    if (
        not normalized
        or normalized in pending_values
        or normalized.startswith(("pending", "待", "待補", "待 ai", "未完成", "needs_review"))
    ):
        return "PNG 反審證據缺失；必須列明可見文字逐項反審、Bauhaus 風格反審、footer 反審、失敗條件或反例檢查。"
    contract, contract_error = infographic_reverse_review_contract()
    if contract_error:
        return contract_error
    required_groups = (
        ("可見文字", "圖內文字", "visible text"),
        ("逐項", "逐句", "逐字", "line-by-line"),
        ("反例", "失敗條件", "不通過", "fail"),
    )
    missing = [
        "/".join(group)
        for group in required_groups
        if not any(marker.casefold() in value.casefold() for marker in group)
    ]
    contract_markers = [contract.get("normative_owner", "")]
    if contract.get("style_enforcement") != "advisory_only":
        contract_markers.append(contract.get("required_style_basis", ""))
    if provided_original:
        required_groups += (("原圖", "使用者提供"), ("footer", "註腳"))
    missing.extend(marker for marker in contract_markers if marker and marker not in value)
    try:
        spec_receipt = infographic_spec_read_receipt_text()
    except OSError as exc:
        return f"required infographic spec cannot be read for full-document receipt: {exc}"
    if spec_receipt not in value:
        missing.append("規格全文讀回 receipt: " + spec_receipt)
    if missing:
        return "PNG 反審證據不完整；缺少：" + ", ".join(missing)
    return None


def infographic_reverse_review_contract() -> tuple[dict[str, str], str | None]:
    if not INFOGRAPHIC_SPEC_PATH.exists():
        return {}, f"required infographic spec is missing: {INFOGRAPHIC_SPEC_PATH}"
    try:
        spec = INFOGRAPHIC_SPEC_PATH.read_text(encoding="utf-8")
    except OSError as exc:
        return {}, f"required infographic spec cannot be read: {exc}"
    match = re.search(
        r"<!-- BEGIN INFOGRAPHIC_REVERSE_REVIEW_CONTRACT\n(?P<body>[\s\S]*?)\nEND INFOGRAPHIC_REVERSE_REVIEW_CONTRACT -->",
        spec,
    )
    if not match:
        return {}, "required infographic reverse-review contract is missing from docs/資訊圖生成規格.md"
    contract: dict[str, str] = {}
    for raw_line in match.group("body").splitlines():
        if ":" not in raw_line:
            continue
        key, raw_value = raw_line.split(":", 1)
        contract[key.strip()] = raw_value.strip()
    missing_keys = [
        key
        for key in (
            "normative_owner",
            "required_review_axes",
            "required_style_basis",
            "footer_provenance_rule",
            "blocking_failures",
        )
        if not contract.get(key)
    ]
    if missing_keys:
        return contract, "infographic reverse-review contract missing keys: " + ", ".join(missing_keys)
    return contract, None


def infographic_spec_issues(blocks: tuple[str, ...] | list[str]) -> list[dict[str, str]]:
    if not blocks:
        return []
    _contract, contract_error = infographic_reverse_review_contract()
    if contract_error:
        return [{
            "status": "blocked",
            "code": "infographic_reverse_review_contract_invalid",
            "message": contract_error,
        }]
    return []


def infographic_bridge_gaps(data: SliceData) -> list[str]:
    required = {
        "承接邏輯": data.infographic_bridge_logic,
        "連線 / 分組理由": data.infographic_connection_reasons,
        "斷橋檢查": data.infographic_bridge_validation,
        "重要性覆蓋": data.infographic_importance_coverage,
        "本 URL 資訊圖數量判斷": data.infographic_count_decision,
    }
    return [label for label, value in required.items() if incomplete_free_text(value)]


def infographic_duplicate_issues_from_blocks(blocks: tuple[str, ...] | list[str]) -> list[dict[str, str]]:
    if len(blocks) <= 1:
        return []
    profiles = [infographic_profile(block) for block in blocks]
    issues: list[dict[str, str]] = []
    seen_titles: dict[str, int] = {}
    seen_divisions: dict[str, int] = {}
    seen_coverages: dict[str, int] = {}
    seen_text_lines: dict[str, int] = {}
    for index, profile in enumerate(profiles, start=1):
        title_key = comparable_text(profile["title"])
        if not title_key:
            issues.append({"image": str(index), "issue": "missing_title"})
        elif title_key in seen_titles:
            issues.append({"image": str(index), "issue": "duplicate_title", "first_image": str(seen_titles[title_key])})
        else:
            seen_titles[title_key] = index

        division_key = comparable_text(profile["division"])
        if incomplete_free_text(profile["division"]) or len(division_key) < 8:
            issues.append({"image": str(index), "issue": "missing_or_weak_division"})
        elif division_key in seen_divisions:
            issues.append({"image": str(index), "issue": "duplicate_division", "first_image": str(seen_divisions[division_key])})
        else:
            seen_divisions[division_key] = index

        coverage_key = comparable_text(profile["coverage"])
        if coverage_key and coverage_key in seen_coverages:
            issues.append({"image": str(index), "issue": "duplicate_importance_coverage", "first_image": str(seen_coverages[coverage_key])})
        elif coverage_key:
            seen_coverages[coverage_key] = index

        for line in profile["text_lines"]:
            if repeatable_infographic_visible_text(line):
                continue
            line_key = comparable_text(line)
            if len(line_key) < 8:
                continue
            if line_key in seen_text_lines:
                issues.append({"image": str(index), "issue": "duplicate_visible_text", "first_image": str(seen_text_lines[line_key])})
            else:
                seen_text_lines[line_key] = index

    for left_index, left_profile in enumerate(profiles, start=1):
        for right_index, right_profile in enumerate(profiles[left_index:], start=left_index + 1):
            distinct_coverage = profiles_have_distinct_coverage(left_profile, right_profile)
            if high_unit_overlap(left_profile["anchors"], right_profile["anchors"]) and high_unit_overlap(
                left_profile["text_lines"],
                right_profile["text_lines"],
            ):
                issues.append({
                    "image": str(right_index),
                    "issue": "semantic_overlap_with_previous_image",
                    "first_image": str(left_index),
                })
            if not distinct_coverage and high_text_similarity(
                infographic_semantic_text(left_profile),
                infographic_semantic_text(right_profile),
                threshold=0.45,
            ):
                issues.append({
                    "image": str(right_index),
                    "issue": "semantic_profile_overlap",
                    "first_image": str(left_index),
                })
            if not distinct_coverage and high_text_similarity(
                " ".join(str(line) for line in left_profile["text_lines"]),
                " ".join(str(line) for line in right_profile["text_lines"]),
                threshold=0.38,
            ):
                issues.append({
                    "image": str(right_index),
                    "issue": "visible_text_semantic_overlap",
                    "first_image": str(left_index),
                })
    return issues


def repeatable_infographic_visible_text(line: str) -> bool:
    text = str(line).strip()
    if re.fullmatch(r"\d+\s*/\s*\d+", text):
        return True
    normalized = text.replace("：", ":")
    return normalized.startswith("來源:") and "資訊圖:" in normalized


def profiles_have_distinct_coverage(left_profile: dict[str, object], right_profile: dict[str, object]) -> bool:
    left_ids = coverage_ids_from_profile(left_profile)
    right_ids = coverage_ids_from_profile(right_profile)
    if not left_ids or not right_ids or left_ids & right_ids:
        return False
    left_division = comparable_text(left_profile.get("division", ""))
    right_division = comparable_text(right_profile.get("division", ""))
    return bool(left_division and right_division and left_division != right_division)


def coverage_ids_from_profile(profile: dict[str, object]) -> set[str]:
    # Current compiler transports the actual schema-defined IDs as JSON.
    # Do not guess their naming convention from prose (U-01 is as valid as K01).
    # A malformed explicit field must not fall back to incidental text matches.
    if profile.get("coverage_ids_json") is not None:
        try:
            ids = json.loads(str(profile["coverage_ids_json"]))
        except (ValueError, TypeError):
            return set()
        if (not isinstance(ids, list) or not ids
                or any(not isinstance(item, str) or not item.strip() for item in ids)
                or len(set(ids)) != len(ids)):
            return set()
        return set(ids)
    values = [
        str(profile.get("coverage", "")),
        str(profile.get("bridge", "")),
        str(profile.get("division", "")),
    ]
    values.extend(str(item) for item in profile.get("anchors", []))  # type: ignore[arg-type]
    values.extend(str(item) for item in profile.get("text_lines", []))  # type: ignore[arg-type]
    text = " ".join(values)
    # Compatibility notes may use CU-... while Compiler Worker v2 records
    # source coverage as K01, K02, … .  Both carry the same only-once
    # allocation meaning for the duplicate-content guard.
    return set(re.findall(r"\b(?:CU-[A-Z0-9]+-\d+|K\d{2,})\b", text))


def infographic_block_quality_issues_from_blocks(blocks: tuple[str, ...] | list[str]) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []
    for index, block in enumerate(blocks, start=1):
        method = infographic_long_field(
            block,
            ("PNG 生成方式", "PNG generation method", "png_generation_method"),
            default="missing",
        )
        method_issue = png_generation_method_issue(method)
        if method_issue:
            issues.append({"image": str(index), "issue": "invalid_png_generation_method", "message": method_issue})
        status = png_semantic_status(block)
        if incomplete_status(status, {"passed", "pass"}):
            issues.append({"image": str(index), "issue": "png_semantic_validation_not_passed"})
        reverse_status = png_reverse_review_status(block)
        if incomplete_status(reverse_status, {"passed", "pass"}):
            issues.append({"image": str(index), "issue": "png_reverse_review_not_passed"})
        reverse_evidence_issue = png_reverse_review_evidence_issue(
            png_reverse_review_evidence(block),
            provided_original=provided_png_retention_method(
                infographic_long_field(
                    block,
                    ("PNG 生成方式", "PNG generation method", "png_generation_method"),
                    default="missing",
                )
            ),
        )
        if reverse_evidence_issue:
            issues.append({
                "image": str(index),
                "issue": "png_reverse_review_evidence_incomplete",
                "message": reverse_evidence_issue,
            })
        required = {
            "承接邏輯": infographic_long_field(block, ("承接邏輯", "bridge_logic"), default="missing"),
            "連線 / 分組理由": infographic_long_field(block, ("連線 / 分組理由", "connection_reasons"), default="missing"),
            "斷橋檢查": infographic_long_field(block, ("斷橋檢查", "bridge_validation"), default="missing"),
            "重要性覆蓋": infographic_long_field(block, ("重要性覆蓋", "importance_coverage"), default="missing"),
            "本 URL 資訊圖數量判斷": infographic_long_field(block, ("本 URL 資訊圖數量判斷", "image_count_decision"), default="missing"),
        }
        if len(blocks) > 1:
            required["本圖分工"] = infographic_long_field(block, ("本圖分工",), default="missing")
        for label, value in required.items():
            if incomplete_free_text(value):
                issues.append({"image": str(index), "issue": "missing_required_infographic_field", "field": label})
    return issues


def infographic_profile(block: str) -> dict[str, object]:
    coverage_match = re.search(r"^- coverage_ids_json:(.*)$", block, flags=re.MULTILINE)
    return {
        "coverage_ids_json": coverage_match.group(1).strip() if coverage_match else None,
        "title": infographic_long_field(block, ("標題",), default=""),
        "division": infographic_long_field(block, ("本圖分工",), default=""),
        "coverage": infographic_long_field(block, ("重要性覆蓋",), default=""),
        "bridge": infographic_long_field(block, ("承接邏輯",), default=""),
        "connection": infographic_long_field(block, ("連線 / 分組理由",), default=""),
        "bridge_validation": infographic_long_field(block, ("斷橋檢查",), default=""),
        "anchors": field_list_values(block, "原文實體錨點"),
        "text_lines": field_list_values(block, "圖內文字"),
    }


def field_list_values(block: str, label: str) -> list[str]:
    match = re.search(rf"^- {re.escape(label)}:(.*?)(?=^\- |\Z)", block, flags=re.MULTILINE | re.DOTALL)
    if not match:
        return []
    values = []
    for line in match.group(1).splitlines():
        cleaned = re.sub(r"^\s*(?:[-*]|\d+\.)\s*", "", line).strip()
        if cleaned and not incomplete_free_text(cleaned):
            values.append(cleaned)
    return values


def comparable_text(value: object) -> str:
    return re.sub(r"[^\w\u4e00-\u9fff]+", "", str(value).casefold())


def high_unit_overlap(left: object, right: object) -> bool:
    left_units = {comparable_text(item) for item in left if len(comparable_text(item)) >= 4}  # type: ignore[arg-type]
    right_units = {comparable_text(item) for item in right if len(comparable_text(item)) >= 4}  # type: ignore[arg-type]
    if not left_units or not right_units:
        return False
    overlap = len(left_units & right_units)
    return overlap >= min(len(left_units), len(right_units)) and overlap >= 1


def infographic_semantic_text(profile: dict[str, object]) -> str:
    parts = [
        str(profile.get("title", "")),
        str(profile.get("division", "")),
        str(profile.get("coverage", "")),
        str(profile.get("bridge", "")),
        str(profile.get("connection", "")),
        str(profile.get("bridge_validation", "")),
        " ".join(str(item) for item in profile.get("anchors", [])),  # type: ignore[arg-type]
        " ".join(str(item) for item in profile.get("text_lines", [])),  # type: ignore[arg-type]
    ]
    return " ".join(part for part in parts if part.strip())


def high_text_similarity(left: str, right: str, threshold: float) -> bool:
    left_units = semantic_ngrams(left)
    right_units = semantic_ngrams(right)
    if len(left_units) < 8 or len(right_units) < 8:
        return False
    overlap = len(left_units & right_units)
    return (overlap / min(len(left_units), len(right_units))) >= threshold


def semantic_ngrams(value: str, width: int = 3) -> set[str]:
    key = comparable_text(value)
    if len(key) < width:
        return set()
    return {key[index : index + width] for index in range(0, len(key) - width + 1)}


def learning_check_types(text: str) -> set[str]:
    block = section_after(text, "## 學習檢查") or section_after(text, "## 🧪 學習檢查")
    types: set[str] = set()
    for line in block.splitlines():
        if not line.startswith("|") or "---" in line:
            continue
        cells = [clean_table_cell(cell) for cell in line.strip("|").split("|")]
        if not cells or cells[0] in {"題型", "類型"}:
            continue
        types.add(cells[0])
    return types


def learning_check_gaps(text: str) -> list[str]:
    existing = learning_check_types(text)
    return [check_type for check_type in REQUIRED_LEARNING_CHECK_TYPES if check_type not in existing]


MAINLINE_TERM_REPLACEMENTS = {
    "| 外部寫入狀態 | 待寫 Notion |": "| vault_write_status | read_back_verified |",
    "Notion 入庫": "Obsidian vault 入庫",
    "Notion adapter 路線": "ObsidianVaultAdapter 路線",
    "Notion source row": "Obsidian source note",
    "Notion row": "Obsidian source note",
    "Notion-ready": "Obsidian-ready",
    "Markdown 真源": "source note",
    "待寫 Notion": "read_back_verified",
    "把本文轉成 Notion source row、資訊圖預覽和 3 至 5 條知識點": (
        "把本文寫入 Obsidian source note、資訊圖 embed 和 1 至 3 條 knowledge-point notes"
    ),
    "使用者 應用、Notion、Markdown、本機路徑": "使用者 應用、Obsidian、Markdown、本機路徑",
}

NOTION_MAINLINE_RESIDUE = tuple(MAINLINE_TERM_REPLACEMENTS) + ("正式產品流程應統一成 **Notion",)
INTERNAL_ACTION_ROW_KEYS = ("可行動下一步", "仍需驗證")
SOURCE_INTERNAL_ROW_KEYS = ("外部寫入", "是否可入庫")
SOURCE_INTERNAL_RESIDUE = (
    "Notion 入庫",
    "notion 入庫",
    "Notion adapter",
    "Notion source row",
    "Notion row",
    "Notion-ready",
    "Obsidian-ready",
    "Obsidian 入庫",
    "Obsidian vault 入庫",
    "Markdown 真源",
    "本檔產出",
    "vertical slice",
    "入庫流程",
    "正式入庫",
    "待入庫",
    "URL 入庫",
    "文檔入庫",
    "文件入庫",
    "文章入庫",
    "Markdown 入庫",
    "vault_write_status",
    "不可加入:",
    "圖面污染檢查:",
    "驗收句:",
)
SOURCE_NOTE_FRONTMATTER_REPLACEMENTS = {
    "內部入庫流程": "內部流程",
    "入庫流程": "內部流程",
    "本 UAT": "非原文任務",
    "Obsidian 入庫": "Obsidian 處理",
    "內部 pipeline": "其他任務",
    "Obsidian / Markdown 入庫": "Obsidian / Markdown 處理",
    "URL 入庫": "URL 處理",
    "文檔入庫": "文檔處理",
    "文件入庫": "文件處理",
    "文章入庫": "文章處理",
    "Markdown 真源": "來源筆記",
    "正式入庫": "正式處理",
    "待入庫": "待處理",
}


def normalize_mainline_terms(text: str) -> str:
    for old, new in MAINLINE_TERM_REPLACEMENTS.items():
        text = text.replace(old, new)
    return text


def canonical_source_section_span(text: str) -> tuple[int, int] | None:
    """Locate the reviewed full-body section through its closing fence."""
    start = text.find("## 正文保存區")
    if start < 0:
        return None
    match = re.search(
        r"(?ms)^### Canonical article body\s*\n(?P<fence>`{3,})(?:markdown|md|text)?\s*\n.*?\n(?P=fence)",
        text[start:],
    )
    return (start, start + match.end()) if match else None


def source_note_without_canonical_section(text: str) -> str:
    span = canonical_source_section_span(text)
    return text[:span[0]] + text[span[1]:] if span else text


def prepare_source_markdown(text: str, source_id: str) -> str:
    # Source evidence, including reviewed anchors and the original body, must
    # never pass through generated-prose terminology or residue cleanup.
    canonical_span = canonical_source_section_span(text)
    canonical_section = ""
    canonical_placeholder = ""
    if canonical_span:
        start, end = canonical_span
        canonical_section = text[start:end]
        canonical_placeholder = f"CANONICAL_BODY_{hashlib.sha256(canonical_section.encode('utf-8')).hexdigest()}"
        if canonical_placeholder in text:
            raise ValueError("canonical body placeholder collision")
        text = text[:start] + canonical_placeholder + text[end:]
    marker_positions = [text.find(marker) for marker in INDEX_FIELD_MARKERS if marker in text]
    if marker_positions:
        text = text[: min(marker_positions)].rstrip()
    text = normalize_mainline_terms(text)
    text = re.sub(
        r"!\[[^\]]*\]\(assets/([^)]+)\)",
        lambda match: f"![[assets/{source_id.lower()}/{match.group(1)}]]",
        text,
    )
    text = remove_source_internal_lines(text)
    prepared = remove_for_adam_section(text)
    return prepared.replace(canonical_placeholder, canonical_section) if canonical_section else prepared


def extract_application_markdown(text: str) -> str:
    bounds = personal_layer_bounds(text)
    if bounds is None:
        return ""
    start, end = bounds
    block = text[start:end]
    block = remove_internal_action_sections(block.strip())
    block = normalize_mainline_terms(block)
    return block.replace(block.splitlines()[0], "## 個人應用摘要", 1).strip()


def remove_for_adam_section(text: str) -> str:
    bounds = personal_layer_bounds(text)
    if bounds is None:
        return text
    start, end = bounds
    before = text[:start].rstrip()
    after = text[end:].lstrip()
    if not after:
        return before + "\n" if before else ""
    return before + "\n\n" + after if before else after


def personal_layer_bounds(text: str) -> tuple[int, int] | None:
    match = personal_layer_heading_match(text)
    if match is None:
        return None
    next_heading = next_level_two_heading_offset(text, after=match.end())
    end = len(text) if next_heading is None else next_heading
    return match.start(), end


def remove_internal_action_sections(text: str) -> str:
    for heading in ("### 下一步", "### 仍需驗證"):
        start = text.find("\n" + heading)
        if start == -1 and text.startswith(heading):
            start = 0
        if start == -1:
            continue
        next_heading = text.find("\n### ", start + len(heading))
        text = text[:start].rstrip() if next_heading == -1 else text[:start].rstrip() + "\n\n" + text[next_heading:].lstrip()
    return remove_internal_action_rows(text).strip()


def remove_internal_action_rows(text: str) -> str:
    lines = []
    for line in text.splitlines():
        if is_table_row_with_first_cell(line, INTERNAL_ACTION_ROW_KEYS):
            continue
        lines.append(line)
    return "\n".join(lines)


def remove_adam_application_rows(text: str) -> str:
    lines = []
    for line in text.splitlines():
        if line.startswith("|") and ("使用者 應用層" in line or "使用者 應用" in line or "For 使用者" in line):
            continue
        lines.append(line)
    return "\n".join(lines)


def remove_source_internal_lines(text: str) -> str:
    lines = []
    for line in text.splitlines():
        if line.startswith("> 狀態:"):
            continue
        if is_table_row_with_first_cell(line, SOURCE_INTERNAL_ROW_KEYS):
            continue
        if any(marker in line for marker in SOURCE_INTERNAL_RESIDUE):
            continue
        lines.append(line)
    return "\n".join(lines)


def sanitize_source_note_frontmatter_text(value: str) -> str:
    for old, new in SOURCE_NOTE_FRONTMATTER_REPLACEMENTS.items():
        value = value.replace(old, new)
    return value


def is_table_row_with_first_cell(line: str, keys: tuple[str, ...]) -> bool:
    if not line.startswith("|") or "---" in line:
        return False
    cells = [clean_table_cell(cell) for cell in line.strip("|").split("|")]
    return bool(cells) and cells[0] in keys


def markdown_subsection(text: str, heading: str) -> str:
    match = re.search(
        rf"(?ms)^### {re.escape(heading)}\s*\n(.*?)(?=^### |\Z)",
        text,
    )
    return match.group(1).strip() if match else ""


def bullet_label(section: str, label: str, default: str = "") -> str:
    for line in section.splitlines():
        stripped = line.strip()
        stripped = re.sub(r"^[-*]\s*", "", stripped)
        stripped = re.sub(r"^\d+\.\s*", "", stripped)
        for sep in (":", "："):
            prefix = f"{label}{sep}"
            if stripped.startswith(prefix):
                return stripped[len(prefix):].strip()
    return default


def concrete_list_items(section: str) -> list[str]:
    items: list[str] = []
    for line in section.splitlines():
        stripped = line.strip()
        if not re.match(r"^([-*]|\d+\.)\s+", stripped):
            continue
        item = re.sub(r"^([-*]|\d+\.)\s+", "", stripped).strip()
        if not item:
            continue
        if re.match(r"^(狀態|原因|一句原因|類型|下一步|扣連)[:：]", item):
            continue
        if any(marker in item for marker in APPLICATION_DRAFT_PLACEHOLDERS):
            continue
        if any(marker in item for marker in APPLICATION_NON_LINK_PLACEHOLDERS):
            continue
        items.append(item)
    return items


def prefixed_list_items(section: str, label: str) -> list[str]:
    items: list[str] = []
    for line in section.splitlines():
        stripped = line.strip()
        stripped = re.sub(r"^[-*]\s*", "", stripped)
        stripped = re.sub(r"^\d+\.\s*", "", stripped)
        for sep in (":", "："):
            prefix = f"{label}{sep}"
            if stripped.startswith(prefix):
                value = stripped[len(prefix):].strip()
                if value and not any(marker in value for marker in APPLICATION_DRAFT_PLACEHOLDERS):
                    items.append(value)
    return items


def split_compounding_types(value: str) -> list[str]:
    parts = [part.strip() for part in re.split(r"[/、,，;；]", value) if part.strip()]
    return [part for part in parts if part in VALID_COMPOUNDING_TYPES]


def has_application_placeholders(text: str) -> bool:
    lowered = text.casefold()
    if any(marker.casefold() in lowered for marker in APPLICATION_DRAFT_PLACEHOLDERS if marker != "pending"):
        return True
    # A technical explanation may discuss a pending state. Only a standalone
    # draft value is unfinished; do not treat every occurrence as a placeholder.
    for line in text.splitlines():
        for cell in (line.split("|") if line.lstrip().startswith("|") else [line]):
            value = re.sub(r"^(?:[-*]|\d+[.)])\s+", "", cell.strip())
            value = re.split(r"[:：]", value)[-1].strip(" *`_。.!！")
            if value.casefold() == "pending":
                return True
    return False


def application_reader_residue_terms(text: str) -> list[str]:
    return [marker for marker in APPLICATION_READER_RESIDUE if marker in text]


def normalize_application_reader_residue(text: str) -> str:
    # Do not rewrite an unfinished judgment into a seemingly reviewed default.
    # The incoming writer gate must see the original residue and fail closed.
    return text


def application_metadata(data: SliceData) -> dict[str, object]:
    app = data.application_markdown
    semantic_match = re.search(r"^<!-- application_semantics_json: (.+) -->$", app, re.M)
    semantic = json.loads(semantic_match[1]) if semantic_match else {}
    if not isinstance(semantic, dict):
        raise AdapterStop("blocked", "invalid application semantics projection")
    adoption_section = markdown_subsection(app, "採納判斷") or markdown_subsection(app, "採納草案")
    personal_section = markdown_subsection(app, "既有知識扣連")
    taste_section = markdown_subsection(app, "品味扣連")
    taste_signal_section = markdown_subsection(app, "品味訊號")
    taste_exclusion_section = markdown_subsection(app, "品味排除訊號")
    anti_algorithm_section = markdown_subsection(app, "反演算法檢查")
    exploration_section = markdown_subsection(app, "探索意圖")
    compounding_section = markdown_subsection(app, "跨來源洞見判斷")

    ai_adoption_status = bullet_label(
        adoption_section,
        "狀態",
        DEFAULT_AI_ADOPTION_STATUS,
    )
    ai_adoption_reason = (
        bullet_label(adoption_section, "一句原因")
        or bullet_label(adoption_section, "原因")
        or DEFAULT_AI_ADOPTION_REASON
    )

    personalization_links = concrete_list_items(personal_section)
    personalization_status = "linked" if personalization_links else DEFAULT_PERSONALIZATION_STATUS

    taste_links = list(dict.fromkeys(concrete_list_items(taste_section) + prefixed_list_items(taste_section, "扣連")))
    taste_status = bullet_label(taste_section, "狀態")
    if not taste_status:
        if data.taste_network_status in {"否", "不入網"}:
            taste_status = DEFAULT_TASTE_LINK_STATUS
        elif taste_links:
            taste_status = "linked"
        else:
            taste_status = "taste_needs_context"

    is_not_taste = data.taste_network_status in {"否", "不入網", "no", "not_applicable", "不適用"}
    taste_signals = concrete_list_items(taste_signal_section)
    taste_exclusion_signals = concrete_list_items(taste_exclusion_section)
    if is_not_taste and not taste_exclusion_signals:
        taste_exclusion_signals = [
            f"不入品味網：{data.taste_network_reason}",
            "保留為工作與學習判斷材料，不作生活或審美偏好證據。",
            "避免把知識接觸面誤作 使用者 已確認的個人品味。",
        ]

    anti_algorithm_status = bullet_label(anti_algorithm_section, "狀態")
    anti_algorithm_judgment = bullet_label(anti_algorithm_section, "判斷")
    if anti_algorithm_status and anti_algorithm_judgment:
        anti_algorithm_status = f"{anti_algorithm_status}：{anti_algorithm_judgment}"
    if is_not_taste and not anti_algorithm_status:
        anti_algorithm_status = "已檢查：此來源不因熱門度進入品味網，只保留可追溯的工作與學習價值。"
    anti_algorithm_status = anti_algorithm_status or "未能判斷"

    exploration_intent = bullet_label(exploration_section, "意圖")
    exploration_reason = bullet_label(exploration_section, "一句原因") or bullet_label(exploration_section, "原因")
    if exploration_intent and exploration_reason:
        exploration_intent = f"{exploration_intent}：{exploration_reason}"
    if is_not_taste and not exploration_intent:
        exploration_intent = "不適用：此來源不進品味探索網，後續行動放在知識與工作流程。"
    exploration_intent = exploration_intent or "未能判斷"

    compounding_status = bullet_label(compounding_section, "狀態", DEFAULT_COMPOUNDING_STATUS)
    compounding_types = []
    for value in prefixed_list_items(compounding_section, "類型"):
        for insight_type in split_compounding_types(value):
            if insight_type not in compounding_types:
                compounding_types.append(insight_type)
    compounding_links = prefixed_list_items(compounding_section, "扣連")
    compounding_next_action = (
        bullet_label(compounding_section, "下一步")
        or DEFAULT_COMPOUNDING_NEXT_ACTION
    )
    compounding_reader_summary = markdown_subsection(app, "洞見")

    related_match = re.search(r"^<!-- related_sources_json: (.+) -->$", app, re.M)
    related_json = related_match[1] if related_match else "[]"
    json.loads(related_json)  # Reject malformed structured relationship data.
    return {
        "personal_relevance": semantic.get("personal_relevance", "未知"),
        "personal_relevance_reason": semantic.get("personal_relevance_reason", "未提供可核對的個人脈絡判斷。"),
        "action_status": "需脈絡" if compounding_status == "needs_context" or not personalization_links else
                         "不適用" if ai_adoption_status == "拒絕" else "可應用",
        "idea_candidate_status": semantic.get("idea_candidate_status", "needs_review"),
        "reusable_concepts_json": json.dumps([
            {"name": point.reusable_concept_name, "reason": point.reusable_concept_reason,
             "kp_id": point.kp_id, "evidence": point.evidence}
            for point in data.knowledge_points if point.reusable_concept_status == "reusable"], ensure_ascii=False),
        "related_sources_json": related_json,
        "ai_adoption_status": ai_adoption_status,
        "ai_adoption_reason": ai_adoption_reason,
        "personalization_status": personalization_status,
        "personalization_links": personalization_links,
        "taste_link_status": taste_status,
        "taste_links": taste_links,
        "taste_signals": taste_signals,
        "taste_exclusion_signals": taste_exclusion_signals,
        "anti_algorithm_status": anti_algorithm_status,
        "exploration_intent": exploration_intent,
        "compounding_insight_status": compounding_status,
        "compounding_insight_types": compounding_types,
        "compounding_insight_links": compounding_links,
        "compounding_next_action": compounding_next_action,
        "_compounding_section": compounding_section,
        "_compounding_reader_summary": compounding_reader_summary,
        "_verified_cross_source_links": (
            len(getattr(data, "cross_source_links", ())) if getattr(data, "prior_knowledge_applies", False) else None
        ),
    }


def split_knowledge_use(value: str) -> list[str]:
    parts = [part.strip() for part in re.split(r"[/、,，;；]", value) if part.strip()]
    return parts or ["事實真源"]


def slice_enum_violations(data: SliceData) -> list[dict[str, str]]:
    violations: list[dict[str, str]] = []
    if data.asset_mode not in {ASSET_MODE_FORMAL, ASSET_MODE_CASE, ASSET_MODE_KNOWLEDGE}:
        violations.append(enum_violation("asset_mode", data.asset_mode, {ASSET_MODE_FORMAL, ASSET_MODE_CASE, ASSET_MODE_KNOWLEDGE}))
    if data.source_input_kind not in VALID_SOURCE_INPUT_KINDS:
        violations.append(enum_violation("source_input_kind", data.source_input_kind, VALID_SOURCE_INPUT_KINDS))
    if data.source_retention_mode not in VALID_SOURCE_RETENTION_MODES:
        violations.append(enum_violation("source_retention_mode", data.source_retention_mode, VALID_SOURCE_RETENTION_MODES))
    if data.source_type not in VALID_SOURCE_TYPES:
        violations.append(enum_violation("source_type", data.source_type, VALID_SOURCE_TYPES))
    for value in data.knowledge_use:
        if value not in VALID_KNOWLEDGE_USE:
            violations.append(enum_violation("knowledge_use", value, VALID_KNOWLEDGE_USE))
    return violations


def enum_violation(field: str, value: str, allowed: set[str]) -> dict[str, str]:
    return {
        "field": field,
        "value": value,
        "allowed_values": " / ".join(sorted(allowed)),
        "message": f"{field} value is outside the allowed enum: {value}",
    }


def formal_source_id(source_id: str) -> bool:
    return bool(re.fullmatch(r"KS-VS-[A-Z0-9-]+", source_id.strip().upper()))


def source_slice_slug(source_slice: Path, source_id: str) -> str:
    stem = source_slice.stem
    prefix = f"{source_id}_"
    if stem.casefold().startswith(prefix.casefold()):
        return stem[len(prefix):]
    return stem


def looks_like_canonical_infographic_asset(name: str, source_id: str) -> bool:
    prefix = re.escape(source_id.strip().upper())
    return bool(
        re.fullmatch(
            rf"{prefix}_[a-z0-9_]+_source(?:_\d{{2}})?\.png",
            name,
            flags=re.IGNORECASE,
        )
    )


def vault_infographic_asset_filename(
    source_slice: Path,
    source_id: str,
    asset_path: Path,
    page_index: int,
    page_count: int,
) -> str:
    if not formal_source_id(source_id):
        return asset_path.name
    if looks_like_canonical_infographic_asset(asset_path.name, source_id):
        return asset_path.name
    return canonical_infographic_asset_filename(
        source_id,
        source_slice_slug(source_slice, source_id),
        page_index,
        page_count,
    )


def rewrite_infographic_embeds(
    body: str,
    data: SliceData,
    asset_dests: list[Path],
) -> str:
    # Preserve fenced source bytes. A Markdown image in the original article is
    # evidence, not an instruction to rewrite the preserved article's content.
    parts = re.split(r"(^[ \t]*(`{3,}|~{3,})[^\r\n]*\r?\n)", body, flags=re.MULTILINE)
    if len(parts) > 1:
        output: list[str] = []
        fence: str | None = None
        lines = body.splitlines(keepends=True)
        editable: list[str] = []
        for line in lines:
            match = re.match(r"^[ \t]*(`{3,}|~{3,})", line)
            if match:
                marker = match.group(1)
                if fence is None:
                    if editable:
                        output.append(rewrite_infographic_embeds("".join(editable), data, asset_dests))
                        editable = []
                    fence = marker
                elif marker[0] == fence[0] and len(marker) >= len(fence) and not line[match.end():].strip():
                    fence = None
                output.append(line)
            elif fence is not None:
                output.append(line)
            else:
                editable.append(line)
        if editable:
            output.append(rewrite_infographic_embeds("".join(editable), data, asset_dests))
        return "".join(output)
    source_asset_destinations = {
        asset_src.name: vault_relative(asset_dest)
        for asset_src, asset_dest in zip(data.asset_paths, asset_dests)
    }

    def rewrite_markdown_image(match: re.Match[str]) -> str:
        raw_target = match.group(2).strip()
        local_target = raw_target.split("?", 1)[0].split("#", 1)[0].replace("\\", "/")
        destination = source_asset_destinations.get(Path(local_target).name)
        return f"![[{destination}]]" if destination else match.group(0)

    # User-supplied project image bundles commonly use a package-local folder
    # such as `provided_assets/`, while older slices use `assets/`.  Resolve
    # both forms from the discovered assets rather than leaving a reader note
    # linked to a package-only path.
    body = re.sub(r"!\[([^\]]*)\]\(([^)\r\n]+)\)", rewrite_markdown_image, body)
    for asset_src, asset_dest in zip(data.asset_paths, asset_dests):
        old_rel = f"assets/{data.source_id.lower()}/{asset_src.name}"
        new_rel = vault_relative(asset_dest)
        body = body.replace(f"![[{old_rel}]]", f"![[{new_rel}]]")
        body = body.replace(old_rel, new_rel)
    return body


def rewrite_case_evidence_assets(body: str, asset_srcs: list[Path], asset_dests: list[Path]) -> str:
    """Rewrite only case-package references; leave canonical source fences unchanged."""
    parts = re.split(r"(^[ \t]*(`{3,}|~{3,})[^\r\n]*\r?\n)", body, flags=re.MULTILINE)
    if len(parts) > 1:
        output: list[str] = []
        fence: str | None = None
        editable: list[str] = []
        for line in body.splitlines(keepends=True):
            match = re.match(r"^[ \t]*(`{3,}|~{3,})", line)
            if match:
                marker = match.group(1)
                if fence is None:
                    if editable:
                        output.append(rewrite_case_evidence_assets("".join(editable), asset_srcs, asset_dests))
                        editable = []
                    fence = marker
                elif marker[0] == fence[0] and len(marker) >= len(fence) and not line[match.end():].strip():
                    fence = None
                output.append(line)
            elif fence is not None:
                output.append(line)
            else:
                editable.append(line)
        if editable:
            output.append(rewrite_case_evidence_assets("".join(editable), asset_srcs, asset_dests))
        return "".join(output)
    destinations = {source.name: vault_relative(destination) for source, destination in zip(asset_srcs, asset_dests)}

    def destination_for(raw: str) -> str | None:
        target = raw.strip().split("?", 1)[0].split("#", 1)[0].replace("\\", "/")
        return destinations.get(Path(target).name)

    def rewrite_image(match: re.Match[str]) -> str:
        destination = destination_for(match.group(2))
        return f"![[{destination}]]" if destination else match.group(0)

    def rewrite_link(match: re.Match[str]) -> str:
        destination = destination_for(match.group(2))
        return f"[{match.group(1)}]({destination})" if destination else match.group(0)

    body = re.sub(r"!\[([^\]]*)\]\(([^)\r\n]+)\)", rewrite_image, body)
    return re.sub(r"(?<!!)\[([^\]]+)\]\(([^)\r\n]+)\)", rewrite_link, body)


def build_plan(root: Path, vault: Path, source_slice: Path, data: SliceData) -> dict[str, object]:
    if not re.fullmatch(r"[A-Za-z0-9_-]+", data.source_id) or any(
        not re.fullmatch(r"KP-[A-Za-z0-9_-]+", kp.kp_id) for kp in data.knowledge_points
    ):
        raise AdapterStop("blocked", "unsafe source or knowledge-point identifier")
    registry_resolution = resolve_source_registry(vault, data)
    source_note = registry_resolution.source_note
    identity = registry_resolution.source_identity
    asset_dir = vault / "assets" / data.source_id.lower()
    asset_dests = [
        asset_dir
        / vault_infographic_asset_filename(
            source_slice,
            data.source_id,
            asset_path,
            index,
            len(data.asset_paths),
        )
        for index, asset_path in enumerate(data.asset_paths, start=1)
    ]
    case_asset_srcs = list(data.case_evidence_asset_paths)
    case_asset_dests = [
        asset_dir / "case-evidence" / f"{index:02d}-{re.sub(r'[^A-Za-z0-9_.-]+', '-', asset_path.name)}"
        for index, asset_path in enumerate(case_asset_srcs, start=1)
    ]
    case_image_dests = [
        destination
        for source, destination in zip(case_asset_srcs, case_asset_dests)
        if source in data.case_evidence_image_paths
    ]
    asset_dest = asset_dests[0] if asset_dests else (case_image_dests[0] if case_image_dests else None)
    source_image_dest = (
        asset_dir / f"{data.source_id.lower()}_source_original{data.source_image_path.suffix.lower()}"
        if data.source_image_path
        else None
    )
    kp_notes = [
        vault / "knowledge-points" / "by-source" / data.source_id / f"{kp.kp_id}.md"
        for kp in data.knowledge_points
    ]
    kp_registry = read_knowledge_point_registry(vault)
    duplicate_of_by_kp = duplicate_candidates(kp_registry, data)
    active_kp_ids = {kp.kp_id for kp in data.knowledge_points}
    registered_relations = read_relation_registry(vault)
    active_relations = [
        relation for relation in data.relations
        if relation.source_kp_id in active_kp_ids and relation.target_kp_id in active_kp_ids
        and registered_relations.get(relation_id(data.source_id, relation), {}).get("status", "linked") == "linked"
    ]
    active_cross_links = [
        link for link in data.cross_source_links
        if link.source_kp_id in active_kp_ids
        and registered_relations.get(cross_source_link_id(data.source_id, link), {}).get("status", "linked") == "linked"
    ]
    cross_link_issues = [
        f"{link.source_kp_id} -> {link.target_kp_uid}"
        for link in active_cross_links
        if kp_registry.get(link.target_kp_uid, {}).get("status") != "linked"
        or kp_registry[link.target_kp_uid].get("source_id") == data.source_id
    ]
    data = replace(data, relations=active_relations, cross_source_links=tuple(active_cross_links),
                   source_markdown=project_active_relation_markdown(data.source_markdown, active_relations))
    relations_by_source = relations_grouped_by_source(active_relations)
    application_note = sharded_application_note(vault, data)
    base_files = [
        vault / "bases" / "sources-recent.base",
        vault / "bases" / "sources-review.base",
        vault / "bases" / "knowledge-points-by-source.base",
        vault / "bases" / "applications-review.base",
        vault / "bases" / "入庫總覽.base",
        vault / "bases" / "品味探索.base",
        vault / "bases" / "概念地圖.base",
        vault / "bases" / "maintenance.base",
    ]
    source_body = render_source_note(
        data, source_slice, source_note, asset_dests, case_asset_srcs, case_asset_dests, source_image_dest
    )
    application_body = render_application_note(data, source_note, application_note, asset_dest)
    kp_bodies = [
        render_kp_note(
            kp,
            source_note,
            duplicate_of_by_kp.get(kp.kp_id, ""),
            relations_by_source.get(kp.kp_id, []),
        )
        for kp in data.knowledge_points
    ]
    return {
        "root": root,
        "vault": vault,
        "data": data,
        "source_slice": source_slice,
        "source_identity": identity,
        "registry_file": source_registry_file(vault),
        "knowledge_point_registry_file": knowledge_point_registry_file(vault),
        "relation_registry_file": relation_registry_file(vault),
        "registry_resolution": registry_resolution.mode,
        "registry_issue": registry_resolution.issue,
        "source_note": source_note,
        "source_body": source_body,
        "application_note": application_note,
        "application_body": application_body,
        "asset_src": data.asset_path,
        "asset_srcs": list(data.asset_paths),
        "asset_dest": asset_dest,
        "asset_dests": asset_dests,
        "case_asset_srcs": case_asset_srcs,
        "case_asset_dests": case_asset_dests,
        "case_image_dests": case_image_dests,
        "source_image_src": data.source_image_path,
        "source_image_dest": source_image_dest,
        "knowledge_point_notes": kp_notes,
        "knowledge_point_bodies": kp_bodies,
        "knowledge_point_duplicate_of": duplicate_of_by_kp,
        "relations": active_relations,
        "cross_source_links": active_cross_links,
        "cross_link_issues": cross_link_issues,
        "base_files": base_files,
        "updated_existing": source_note.exists(),
    }


def preflight_report(plan: dict[str, object]) -> dict[str, object]:
    data = plan["data"]
    assert isinstance(data, SliceData)
    source_body = str(plan["source_body"])
    metadata = application_metadata(data)
    issues: list[dict[str, str]] = []
    if data.asset_mode == ASSET_MODE_FORMAL:
        issues.extend(infographic_spec_issues(data.infographic_blocks))

    registry_issue = plan["registry_issue"]
    if registry_issue:
        issues.append({
            "status": "needs_review",
            "code": "registry_mismatch",
            "message": str(registry_issue),
        })

    if data.readability_status in BLOCKING_READABILITY_STATUSES:
        status = "blocked" if data.readability_status == "source_blocked" else "needs_review"
        issues.append({
            "status": status,
            "code": "source_not_writeable",
            "message": f"readability_status is {data.readability_status}; formal vault write is blocked",
        })

    if data.source_input_kind not in VALID_SOURCE_INPUT_KINDS:
        issues.append({
            "status": "needs_review",
            "code": "invalid_source_input_kind",
            "message": f"source_input_kind must be one of {sorted(VALID_SOURCE_INPUT_KINDS)}",
        })
    elif data.source_input_kind == "url" and not data.source_url:
        issues.append({
            "status": "needs_review",
            "code": "missing_source_url",
            "message": "URL source requires a source_url",
        })
    elif data.source_input_kind == "url":
        derived_identity = source_identity(data)
        if data.declared_source_identity and data.declared_source_identity != derived_identity:
            issues.append({
                "status": "blocked",
                "code": "source_identity_mismatch",
                "message": "slice declared source identity does not match its canonical URL identity",
            })
        if data.source_url_binding_status not in {"legacy", "verified"}:
            issues.append({
                "status": "needs_review",
                "code": "source_url_binding_unverified",
                "message": "URL source must have a verified URL-to-capture binding before formal vault write",
            })
        elif data.source_url_binding_status == "verified":
            issues.extend(url_provenance_issues(plan, data))
    elif data.source_input_kind == "user_provided_text":
        derived_identity = source_identity(data)
        if data.source_url:
            issues.append({
                "status": "blocked",
                "code": "user_text_url_forbidden",
                "message": "user-provided text must not declare a URL",
            })
        if data.declared_source_identity != derived_identity:
            issues.append({
                "status": "blocked",
                "code": "source_identity_mismatch",
                "message": "slice declared source identity does not match its content SHA-256 identity",
            })
        if data.readability_status != "user_text_complete":
            issues.append({
                "status": "needs_review",
                "code": "user_text_not_formally_reviewed",
                "message": "user-provided text must use readability_status user_text_complete before formal vault write",
            })
        issues.extend(user_text_provenance_issues(plan, data))
    elif data.source_input_kind == "user_provided_image":
        derived_identity = source_identity(data)
        if data.source_url:
            issues.append({
                "status": "blocked",
                "code": "user_image_url_forbidden",
                "message": "user-provided image must not declare a URL",
            })
        if data.declared_source_identity != derived_identity:
            issues.append({
                "status": "blocked",
                "code": "source_identity_mismatch",
                "message": "slice declared source identity does not match its raw image SHA-256 identity",
            })
        if data.readability_status != "user_image_complete":
            issues.append({
                "status": "needs_review",
                "code": "user_image_not_formally_reviewed",
                "message": "user-provided image must use readability_status user_image_complete before formal vault write",
            })
        issues.extend(user_image_provenance_issues(plan, data))

    if data.source_retention_mode not in VALID_SOURCE_RETENTION_MODES:
        issues.append({
            "status": "needs_review",
            "code": "invalid_source_retention_mode",
            "message": f"source_retention_mode must be one of {sorted(VALID_SOURCE_RETENTION_MODES)}",
        })
    elif data.source_retention_mode == "full_body_required":
        issues.extend(full_body_requirement_issues(plan, data))
    elif data.source_retention_mode == TRANSCRIPT_ARCHIVE_MODE:
        issues.extend(transcript_archive_issues(plan, data))
    elif data.source_retention_mode == "original_image_required":
        if data.source_input_kind != "user_provided_image":
            issues.append({
                "status": "blocked",
                "code": "original_image_mode_forbidden",
                "message": "original_image_required is reserved for user-provided image sources",
            })
        elif not data.source_image_path:
            issues.append({
                "status": "needs_review",
                "code": "source_original_image_missing",
                "message": "user-provided image must retain a readable original image before formal vault write",
            })

    if incomplete_status(data.ai_source_analysis_status, {"complete", "completed"}):
        issues.append({
            "status": "needs_review",
            "code": "ai_source_analysis_incomplete",
            "message": (
                "AI source analysis must be complete before formal vault write; "
                f"current status is {data.ai_source_analysis_status}"
            ),
        })

    for violation in slice_enum_violations(data):
        issues.append({
            "status": "needs_review",
            "code": "enum_violation",
            **violation,
        })

    if not data.knowledge_points:
        issues.append({
            "status": "needs_review",
            "code": "missing_knowledge_points",
            "message": "slice has no knowledge-point rows to write",
        })

    if not data.summary:
        issues.append({
            "status": "needs_review",
            "code": "missing_summary",
            "message": "slice TL;DR summary is empty",
        })

    asset_srcs = plan.get("asset_srcs", [])
    asset_dests = plan.get("asset_dests", [])
    if data.asset_mode == ASSET_MODE_FORMAL:
        if not asset_dests:
            issues.append({
                "status": "needs_review",
                "code": "missing_infographic_asset",
                "message": "slice has no readable infographic asset for the source note",
            })
        else:
            invalid_assets = [
                {
                    "asset": str(asset_src),
                    "issue": png_file_issue(asset_src) if isinstance(asset_src, Path) else "invalid_png_asset",
                }
                for asset_src in asset_srcs
                if not isinstance(asset_src, Path) or png_file_issue(asset_src) is not None
            ]
            if invalid_assets:
                issues.append({
                    "status": "needs_review",
                    "code": "invalid_infographic_asset",
                    "invalid_assets": invalid_assets,
                    "message": "every infographic asset must be a real opaque PNG file, not only a .png filename or transparent PNG",
                })
    elif data.asset_mode == ASSET_MODE_CASE:
        case_srcs = plan.get("case_asset_srcs", [])
        case_dests = plan.get("case_asset_dests", [])
        if not data.case_evidence_manifest_path or not case_srcs or len(case_srcs) != len(case_dests):
            issues.append({
                "status": "blocked",
                "code": "case_evidence_assets_missing",
                "message": "case evidence requires one revalidated manifest and every package-local asset",
            })
        if not data.case_evidence_image_paths or any(png_file_issue(path) is not None for path in data.case_evidence_image_paths):
            issues.append({
                "status": "blocked",
                "code": "case_evidence_screenshots_invalid",
                "message": "case evidence screenshots must remain readable opaque PNG files after manifest validation",
            })

    method_issue = png_generation_method_issue(data.png_generation_method) if data.asset_mode == ASSET_MODE_FORMAL else None
    if method_issue:
        issues.append({
            "status": "needs_review",
            "code": "png_generation_method_invalid",
            "message": method_issue,
        })

    if data.asset_mode == ASSET_MODE_FORMAL and incomplete_status(data.png_semantic_validation_status, {"passed", "pass"}):
        issues.append({
            "status": "needs_review",
            "code": "png_semantic_validation_incomplete",
            "message": (
                "AI PNG semantic validation must pass before formal vault write; "
                f"current status is {data.png_semantic_validation_status}"
            ),
        })

    if data.asset_mode == ASSET_MODE_FORMAL and incomplete_status(data.png_reverse_review_status, {"passed", "pass"}):
        issues.append({
            "status": "needs_review",
            "code": "png_reverse_review_incomplete",
            "message": (
                "PNG reverse review must pass before formal vault write; "
                f"current status is {data.png_reverse_review_status}"
            ),
        })

    reverse_evidence_issue = (
        png_reverse_review_evidence_issue(
            data.png_reverse_review_evidence,
            provided_original=provided_png_retention_method(data.png_generation_method),
        )
        if data.asset_mode == ASSET_MODE_FORMAL
        else None
    )
    if reverse_evidence_issue:
        issues.append({
            "status": "needs_review",
            "code": "png_reverse_review_evidence_incomplete",
            "message": reverse_evidence_issue,
        })

    bridge_gaps = infographic_bridge_gaps(data) if data.asset_mode == ASSET_MODE_FORMAL else []
    if bridge_gaps:
        issues.append({
            "status": "needs_review",
            "code": "infographic_bridge_validation_incomplete",
            "missing_fields": ", ".join(bridge_gaps),
            "message": (
                "infographic bridge validation must explain why visible points are connected; "
                "correct fragments without continuity cannot be written to the formal vault"
            ),
        })

    block_quality_issues = infographic_block_quality_issues_from_blocks(data.infographic_blocks) if data.asset_mode == ASSET_MODE_FORMAL else []
    if block_quality_issues:
        issues.append({
            "status": "needs_review",
            "code": "infographic_block_validation_incomplete",
            "block_issues": json.dumps(block_quality_issues, ensure_ascii=False),
            "message": "every infographic block must pass generation-method, semantic, bridge, division, and coverage validation",
        })

    coverage_scope_issues = infographic_coverage_scope_issues(data, source_body) if data.asset_mode == ASSET_MODE_FORMAL else []
    issues.extend(coverage_scope_issues)

    duplicate_issues = infographic_duplicate_issues_from_blocks(data.infographic_blocks) if data.asset_mode == ASSET_MODE_FORMAL else []
    if duplicate_issues:
        issues.append({
            "status": "needs_review",
            "code": "infographic_duplicate_content",
            "duplicate_issues": json.dumps(duplicate_issues, ensure_ascii=False),
            "message": (
                "multiple infographics for the same source must have distinct visible text, "
                "distinct semantic division, and non-overlapping coverage; repeated content cannot be written"
            ),
        })

    learning_gaps = learning_check_gaps(source_body)
    if learning_gaps:
        issues.append({
            "status": "needs_review",
            "code": "learning_check_incomplete",
            "missing_types": ", ".join(learning_gaps),
            "message": "learning check must include 理解題, 保持題, 遷移題, and 應用任務 before formal vault write",
        })

    if data.application_markdown and has_application_placeholders(data.application_markdown):
        issues.append({
            "status": "needs_review",
            "code": "application_draft_incomplete",
            "message": "application note adoption, personalization, or taste-link draft still contains AI placeholders",
        })

    if plan.get("cross_link_issues"):
        issues.append({
            "status": "blocked",
            "code": "cross_link_target_unavailable",
            "message": "cross-source link target is not a linked knowledge point of an older source: "
                       + "; ".join(str(item) for item in plan["cross_link_issues"]),
        })
    compounding_issues = compounding_metadata_issues(metadata)
    if metadata["taste_link_status"] == "linked" and not metadata["taste_links"]:
        issues.append({
            "status": "needs_review",
            "code": "taste_link_evidence_missing",
            "message": "linked taste status requires concrete taste links before formal vault write",
        })
    if compounding_issues:
        issues.append({
            "status": "needs_review",
            "code": "compounding_insight_incomplete",
            "missing_or_invalid": ", ".join(compounding_issues),
            "message": "application note must include structured cross-source compounding-value judgment before formal vault write",
        })

    if any(marker in source_note_without_canonical_section(source_body) for marker in SOURCE_INTERNAL_RESIDUE):
        issues.append({
            "status": "needs_review",
            "code": "source_layer_pollution",
            "message": "source note body still contains internal workflow or application-layer residue",
        })

    status = "passed"
    if issues:
        status = "blocked" if any(issue["status"] == "blocked" for issue in issues) else "needs_review"
    return {
        "status": status,
        "write_allowed": status == "passed",
        "issues": issues,
    }


def infographic_coverage_scope_issues(data: SliceData, source_body: str) -> list[dict[str, str]]:
    if data.coverage_unit_total <= 0 or data.infographic_page_count <= 0:
        return []

    rows = content_value_coverage_rows(source_body)
    high_rows = [
        row
        for row in rows
        if row.get("importance", "").strip().casefold() in {"high", "高"}
    ]
    high_summary_units = [
        row["id"]
        for row in high_rows
        if row.get("id")
        and "infographic" not in row.get("handling", "").casefold()
    ]
    high_infographic_units = [
        row["id"]
        for row in high_rows
        if row.get("id")
        and "infographic" in row.get("handling", "").casefold()
    ]

    issues: list[dict[str, str]] = []
    if high_summary_units:
        issues.append({
            "status": "needs_review",
            "code": "infographic_core_coverage_unmapped",
            "high_summary_units": ", ".join(high_summary_units),
            "message": (
                "high-importance core coverage units cannot be left outside the infographic; "
                "first decide the extractable scope, then split the required number of images"
            ),
        })
    if not rows and data.coverage_unit_total >= 8 and data.coverage_unit_summary_only > 0:
        issues.append({
            "status": "needs_review",
            "code": "infographic_core_coverage_unmapped",
            "high_summary_units": "summary_only_units_without_content_coverage_rows",
            "message": (
                "coverage audit declares many units and summary-only units, but the content coverage table "
                "does not prove that no high-importance core unit is being skipped"
            ),
        })
    if high_infographic_units and len(high_infographic_units) > data.infographic_page_count * 5:
        issues.append({
            "status": "needs_review",
            "code": "infographic_page_count_too_low_for_core_scope",
            "core_units": ", ".join(high_infographic_units),
            "message": (
                "selected high-importance infographic scope exceeds the 3-5 main-point capacity per page; "
                "increase image count or merge coverage into higher-level core units before generation"
            ),
        })
    return issues


def content_value_coverage_rows(text: str) -> list[dict[str, str]]:
    block = section_after(text, "### 內容價值覆蓋")
    rows: list[dict[str, str]] = []
    for line in block.splitlines():
        if not line.startswith("|") or "---" in line:
            continue
        cells = [clean_table_cell(cell) for cell in line.strip("|").split("|")]
        if len(cells) < 4 or cells[0] in {"覆蓋單元 ID", "覆蓋 ID"}:
            continue
        rows.append({
            "id": cells[0],
            "unit": cells[1],
            "importance": cells[2],
            "handling": cells[3],
        })
    return rows


def sha256_file_text(path: Path) -> str:
    """SHA-256 of a package text archive the way the accepted bundle records it (LF, no final LF)."""
    return hashlib.sha256(path.read_text(encoding="utf-8-sig").rstrip("\n").encode("utf-8")).hexdigest()


def transcript_archive_issues(plan: dict[str, object], data: SliceData, *, written_notes: list[Path] | None = None) -> list[dict[str, str]]:
    """Write/readback gate of the YouTube transcript archive mode.

    The transcript is kept only in the package archive.  Every check here reads
    that archive (not the slice's own claims): both hashes must match the
    accepted bundle, no Vault note may carry a cue timing line, and the
    representative anchors must be reproducible from the archive.
    """
    source_slice = plan["source_slice"]
    assert isinstance(source_slice, Path)
    package = source_slice.parent
    issues: list[dict[str, str]] = []

    def blocked(code: str, message: str) -> None:
        issues.append({"status": "blocked", "code": code, "message": message})

    if data.source_input_kind != "url":
        blocked("transcript_archive_mode_forbidden", "transcript_archive_required is reserved for accepted YouTube caption URL sources")
        return issues
    try:
        bundle = json.loads((package / "input_bundle.json").read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        blocked("transcript_archive_bundle_missing", "transcript archive requires the accepted input_bundle.json beside the slice")
        return issues
    if not isinstance(bundle, dict) or bundle.get("source_retention_mode") != TRANSCRIPT_ARCHIVE_MODE:
        blocked("transcript_archive_bundle_mode_mismatch", "accepted input bundle does not declare transcript_archive_required")
        return issues
    canonical_path = package / "canonical_source.md"
    caption_name = str(bundle.get("youtube_caption_path") or "")
    vtt_path = package / caption_name
    if not canonical_path.is_file():
        blocked("transcript_archive_canonical_missing", "package canonical_source.md is missing")
    elif sha256_file_text(canonical_path) != bundle.get("canonical_body_sha256"):
        blocked("transcript_archive_canonical_hash_mismatch", "package canonical_source.md differs from the accepted canonical_body_sha256")
    if not caption_name:
        blocked("transcript_archive_vtt_path_missing", "accepted input bundle does not name its original caption VTT (youtube_caption_path)")
    elif not vtt_path.is_file():
        blocked("transcript_archive_vtt_missing", "package original caption VTT is missing")
    elif hashlib.sha256(vtt_path.read_bytes()).hexdigest() != bundle.get("youtube_caption_sha256"):
        blocked("transcript_archive_vtt_hash_mismatch", "package original caption VTT differs from the accepted youtube_caption_sha256")
    if data.canonical_body_sha256 != bundle.get("canonical_body_sha256"):
        blocked("transcript_archive_slice_hash_mismatch", "slice canonical body hash differs from the accepted bundle")
    if data.canonical_body_markdown:
        blocked("transcript_archive_body_present", "the slice must not carry a ## 正文保存區 transcript body in this mode")
    if any(issue["status"] == "blocked" for issue in issues):
        return issues
    declared = transcript_archive_declaration(source_slice.read_text(encoding="utf-8-sig"))
    expected_declaration = {
        "保存模式": TRANSCRIPT_ARCHIVE_MODE,
        "正文 SHA-256": str(bundle.get("canonical_body_sha256")),
        "原始字幕 VTT SHA-256": str(bundle.get("youtube_caption_sha256")),
    }
    if any(declared.get(key) != value for key, value in expected_declaration.items()):
        blocked("transcript_archive_declaration_mismatch", "slice ## 字幕存檔 table differs from the accepted bundle hashes or mode")
    canonical = canonical_path.read_text(encoding="utf-8-sig").rstrip("\n")
    if not is_youtube_caption_transcript(canonical):
        blocked("transcript_archive_not_caption_transcript", "package canonical_source.md is not a YouTube caption transcript")
    else:
        expected_anchors = [chunk["text"] for chunk in caption_reading_chunks(canonical)[:3]]
        if not expected_anchors or list(data.canonical_body_representative_anchors) != expected_anchors:
            blocked("transcript_archive_anchor_mismatch", "representative anchors cannot be reproduced from the package archive")
    bodies: list[tuple[str, str]] = [
        ("source note", str(plan.get("source_body") or "")),
        ("application note", str(plan.get("application_body") or "")),
        *((f"knowledge point note {index}", str(body)) for index, body in enumerate(plan.get("knowledge_point_bodies") or [], start=1)),
    ]
    for note in written_notes or []:
        if note.is_file():
            bodies.append((note.name, note.read_text(encoding="utf-8-sig")))
    for label, body in bodies:
        if has_cue_timing_line(body):
            blocked("transcript_archive_cue_timing_line_in_vault", f"{label} contains a caption cue timing line; the transcript must stay in the package archive")
    return issues


def full_body_requirement_issues(plan: dict[str, object], data: SliceData) -> list[dict[str, str]]:
    source_slice = plan["source_slice"]
    assert isinstance(source_slice, Path)
    metadata_path = source_slice.parent / "source_capture_metadata.json"
    review_packet_path = source_slice.parent / "review_packet.json"
    issues: list[dict[str, str]] = []
    body = data.canonical_body_markdown
    if not body:
        issues.append({
            "status": "needs_review",
            "code": "full_body_missing",
            "message": "source_retention_mode is full_body_required but the source slice has no ## 正文保存區 canonical article body",
        })
    if not re.fullmatch(r"[0-9a-f]{64}", data.canonical_body_sha256):
        issues.append({
            "status": "needs_review",
            "code": "full_body_hash_missing",
            "message": "full-body sources require a 64-character 正文保存 SHA-256 generated by the prepare entrypoint",
        })
    elif body and canonical_body_fingerprint(body) != data.canonical_body_sha256:
        issues.append({
            "status": "blocked",
            "code": "full_body_hash_mismatch",
            "message": "canonical article body SHA-256 does not match the reviewed slice declaration",
        })
    if not data.canonical_body_representative_anchors:
        issues.append({
            "status": "needs_review",
            "code": "full_body_representative_anchors_missing",
            "message": "full-body sources require at least one representative body anchor for readback",
        })
    else:
        missing_anchors = [
            anchor
            for anchor in data.canonical_body_representative_anchors
            if not canonical_body_contains_anchor(body, anchor)
        ]
        if missing_anchors:
            issues.append({
                "status": "blocked",
                "code": "full_body_representative_anchor_missing",
                "missing_anchors": " / ".join(missing_anchors[:3]),
                "message": "representative body anchors are not present in the canonical article body",
            })
    packet_issues = full_body_packet_issues(metadata_path, review_packet_path, data)
    return issues + packet_issues


def full_body_packet_issues(metadata_path: Path, review_packet_path: Path, data: SliceData) -> list[dict[str, str]]:
    if not metadata_path.exists() or not review_packet_path.exists():
        return [{
            "status": "needs_review",
            "code": "missing_full_body_provenance_packet",
            "message": "full-body sources require source_capture_metadata.json and review_packet.json from the prepare entrypoint",
        }]
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8-sig"))
        review_packet = json.loads(review_packet_path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        return [{
            "status": "needs_review",
            "code": "invalid_full_body_provenance_packet",
            "message": f"cannot read full-body provenance packet: {exc}",
        }]
    if not isinstance(metadata, dict) or not isinstance(review_packet, dict):
        return [{
            "status": "needs_review",
            "code": "invalid_full_body_provenance_packet",
            "message": "full-body metadata and review packet must be JSON objects",
        }]
    issues: list[dict[str, str]] = []
    for label, packet in (("metadata", metadata), ("review packet", review_packet)):
        if packet.get("source_retention_mode") != "full_body_required":
            issues.append({
                "status": "blocked",
                "code": "full_body_packet_mode_mismatch",
                "message": f"{label} does not declare source_retention_mode=full_body_required",
            })
        if packet.get("canonical_body_required") is not True:
            issues.append({
                "status": "blocked",
                "code": "full_body_packet_required_flag_missing",
                "message": f"{label} does not declare canonical_body_required=true",
            })
        if packet.get("canonical_body_sha256") != data.canonical_body_sha256:
            issues.append({
                "status": "blocked",
                "code": "full_body_packet_hash_mismatch",
                "message": f"{label} canonical_body_sha256 does not match the reviewed slice",
            })
        packet_anchors = packet.get("canonical_body_representative_anchors")
        if not isinstance(packet_anchors, list) or not packet_anchors:
            issues.append({
                "status": "needs_review",
                "code": "full_body_packet_anchors_missing",
                "message": f"{label} has no canonical_body_representative_anchors",
            })
    if data.source_input_kind == "user_provided_text":
        capture = metadata_path.parent / "source_capture.md"
        try:
            original = capture.read_bytes().decode("utf-8-sig").strip().replace("\r\n", "\n").replace("\r", "\n")
        except (OSError, UnicodeError):
            original = None
        if original is None or original != data.canonical_body_markdown.strip().replace("\r\n", "\n").replace("\r", "\n"):
            issues.append({"status": "blocked", "code": "full_body_capture_text_mismatch",
                           "message": "Preserved body must equal the complete accepted text; matching self-declared hashes cannot authorize a summary."})
    return issues


def user_text_provenance_issues(plan: dict[str, object], data: SliceData) -> list[dict[str, str]]:
    source_slice = plan["source_slice"]
    assert isinstance(source_slice, Path)
    capture_path = source_slice.parent / "source_capture.md"
    metadata_path = source_slice.parent / "source_capture_metadata.json"
    review_packet_path = source_slice.parent / "review_packet.json"
    issues: list[dict[str, str]] = []
    if not re.fullmatch(r"[0-9a-f]{64}", data.source_content_sha256):
        return [{
            "status": "needs_review",
            "code": "missing_source_content_sha256",
            "message": "user-provided text requires a 64-character source_content_sha256",
        }]
    if not capture_path.exists() or not metadata_path.exists() or not review_packet_path.exists():
        return [{
            "status": "needs_review",
            "code": "missing_user_text_provenance_packet",
            "message": "user-provided text requires sibling source_capture.md, source_capture_metadata.json, and review_packet.json",
        }]
    actual_hash = hashlib.sha256(capture_path.read_bytes()).hexdigest()
    if actual_hash != data.source_content_sha256:
        issues.append({
            "status": "blocked",
            "code": "source_capture_hash_mismatch",
            "message": "source_capture.md bytes do not match the source_content_sha256 declared in the reviewed slice",
        })
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        return [{
            "status": "needs_review",
            "code": "invalid_user_text_provenance_metadata",
            "message": f"cannot read source_capture_metadata.json: {exc}",
        }]
    if not isinstance(metadata, dict):
        return [{
            "status": "needs_review",
            "code": "invalid_user_text_provenance_metadata",
            "message": "source_capture_metadata.json must be an object",
        }]
    if metadata.get("source_input_kind") != "user_provided_text":
        issues.append({"status": "needs_review", "code": "user_text_metadata_kind_mismatch", "message": "metadata source_input_kind must be user_provided_text"})
    if metadata.get("source_content_sha256") != data.source_content_sha256:
        issues.append({"status": "blocked", "code": "user_text_metadata_hash_mismatch", "message": "metadata source_content_sha256 does not match the reviewed slice"})
    if metadata.get("source_identity") != source_identity(data):
        issues.append({"status": "blocked", "code": "user_text_metadata_identity_mismatch", "message": "metadata source_identity does not match the reviewed slice"})
    if any(str(metadata.get(key) or "").strip() for key in ("url", "source_url")):
        issues.append({"status": "blocked", "code": "user_text_metadata_url_forbidden", "message": "user-provided text metadata must not carry a URL or source_url"})
    if any(key in metadata for key in ("source_path", "input_path", "user_text_file")):
        issues.append({"status": "blocked", "code": "user_text_metadata_path_leak", "message": "direct-text provenance metadata must not persist the original local input path"})
    try:
        review_packet = json.loads(review_packet_path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        return issues + [{
            "status": "needs_review",
            "code": "invalid_user_text_review_packet",
            "message": f"cannot read review_packet.json: {exc}",
        }]
    if not isinstance(review_packet, dict):
        return issues + [{
            "status": "needs_review",
            "code": "invalid_user_text_review_packet",
            "message": "review_packet.json must be an object",
        }]
    if review_packet.get("source_input_kind") != "user_provided_text":
        issues.append({"status": "needs_review", "code": "user_text_review_packet_kind_mismatch", "message": "review packet source_input_kind must be user_provided_text"})
    if review_packet.get("source_content_sha256") != data.source_content_sha256:
        issues.append({"status": "blocked", "code": "user_text_review_packet_hash_mismatch", "message": "review packet source_content_sha256 does not match the reviewed slice"})
    if review_packet.get("source_identity") != source_identity(data):
        issues.append({"status": "blocked", "code": "user_text_review_packet_identity_mismatch", "message": "review packet source_identity does not match the reviewed slice"})
    if any(str(review_packet.get(key) or "").strip() for key in ("source_url", "url")):
        issues.append({"status": "blocked", "code": "user_text_review_packet_url_forbidden", "message": "user-provided text review packet must not carry a URL"})
    if any(key in review_packet for key in ("source_path", "input_path", "user_text_file")):
        issues.append({"status": "blocked", "code": "user_text_review_packet_path_leak", "message": "direct-text review packet must not persist the original local input path"})
    return issues


def user_image_provenance_issues(plan: dict[str, object], data: SliceData) -> list[dict[str, str]]:
    """Require original-image evidence plus a reviewed, coverage-complete visual reading."""
    source_slice = plan["source_slice"]
    assert isinstance(source_slice, Path)
    metadata_path = source_slice.parent / "source_capture_metadata.json"
    review_packet_path = source_slice.parent / "review_packet.json"
    analysis_path = source_slice.parent / "image_analysis.json"
    issues: list[dict[str, str]] = []
    if not re.fullmatch(r"[0-9a-f]{64}", data.source_image_sha256):
        return [{
            "status": "needs_review",
            "code": "missing_source_image_sha256",
            "message": "user-provided image requires a 64-character source image SHA-256",
        }]
    if not data.source_image_path or not metadata_path.exists() or not review_packet_path.exists():
        return [{
            "status": "needs_review",
            "code": "missing_user_image_provenance_packet",
            "message": "user-provided image requires original image, source_capture_metadata.json, and review_packet.json from prepare",
        }]
    try:
        inspection = inspect_image(data.source_image_path)
    except (ImageSourceContractError, OSError) as exc:
        return [{
            "status": "needs_review",
            "code": "invalid_source_original_image",
            "message": f"cannot inspect user-provided original image: {exc}",
        }]
    if inspection.sha256 != data.source_image_sha256:
        issues.append({"status": "blocked", "code": "source_image_hash_mismatch", "message": "retained original image bytes do not match the reviewed source image SHA-256"})
    if data.source_image_mime and inspection.mime_type != data.source_image_mime:
        issues.append({"status": "blocked", "code": "source_image_mime_mismatch", "message": "retained original image MIME does not match the reviewed slice"})
    if (data.source_image_width, data.source_image_height) != (inspection.width, inspection.height):
        issues.append({"status": "blocked", "code": "source_image_dimensions_mismatch", "message": "retained original image dimensions do not match the reviewed slice"})
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8-sig"))
        review_packet = json.loads(review_packet_path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        return issues + [{"status": "needs_review", "code": "invalid_user_image_provenance_packet", "message": f"cannot read user-image provenance packet: {exc}"}]
    if not isinstance(metadata, dict) or not isinstance(review_packet, dict):
        return issues + [{"status": "needs_review", "code": "invalid_user_image_provenance_packet", "message": "user-image metadata and review packet must be JSON objects"}]
    expected_identity = source_identity(data)
    for label, packet in (("metadata", metadata), ("review packet", review_packet)):
        if packet.get("source_input_kind") != "user_provided_image":
            issues.append({"status": "needs_review", "code": "user_image_packet_kind_mismatch", "message": f"{label} source_input_kind must be user_provided_image"})
        if packet.get("source_identity") != expected_identity:
            issues.append({"status": "blocked", "code": "user_image_packet_identity_mismatch", "message": f"{label} source identity does not match the reviewed raw image"})
        if packet.get("source_image_sha256") != data.source_image_sha256:
            issues.append({"status": "blocked", "code": "user_image_packet_hash_mismatch", "message": f"{label} source image SHA-256 does not match the reviewed slice"})
        if packet.get("source_retention_mode") != "original_image_required":
            issues.append({"status": "blocked", "code": "user_image_packet_mode_mismatch", "message": f"{label} must declare original_image_required"})
        if any(str(packet.get(key) or "").strip() for key in ("url", "source_url")):
            issues.append({"status": "blocked", "code": "user_image_packet_url_forbidden", "message": f"{label} for a user image must not carry a URL"})
        if any(key in packet for key in ("source_path", "input_path", "user_image_file")):
            issues.append({"status": "blocked", "code": "user_image_packet_path_leak", "message": f"{label} must not persist the original local image path"})
    if not analysis_path.exists():
        return issues + [{"status": "needs_review", "code": "image_analysis_missing", "message": "user-provided image requires image_analysis.json from the formal image-review step"}]
    try:
        analysis = json.loads(analysis_path.read_text(encoding="utf-8-sig"))
        analysis_contract_issues = validate_visual_analysis(
            analysis,
            expected_image_sha256=inspection.sha256,
            expected_width=inspection.width,
            expected_height=inspection.height,
        )
    except (OSError, json.JSONDecodeError, ImageSourceContractError) as exc:
        return issues + [{"status": "needs_review", "code": "image_analysis_invalid", "message": f"formal image analysis is invalid: {exc}"}]
    if analysis_contract_issues:
        return issues + [{
            "status": "needs_review",
            "code": "image_analysis_invalid",
            "message": "formal image analysis does not satisfy the visual evidence contract",
        }]
    analysis_hash = canonical_analysis_sha256(analysis)
    if review_packet.get("image_analysis_sha256") != analysis_hash:
        issues.append({"status": "blocked", "code": "image_analysis_packet_hash_mismatch", "message": "review packet image_analysis SHA-256 does not match its persisted analysis"})
    if review_packet.get("image_analysis_status") != "complete" or review_packet.get("image_analysis_checker_status") != "passed":
        issues.append({"status": "needs_review", "code": "image_analysis_not_checked", "message": "image-review must record complete analysis and a passed second checker"})
    anchor_ids = {str(anchor.get("id") or "") for anchor in analysis.get("anchors", []) if isinstance(anchor, dict)}
    if not issues:
        try:
            validate_source_anchored_knowledge(
                data.knowledge_points,
                data.relations,
                data.canonical_body_markdown,
                verified_image_anchor_ids=anchor_ids,
            )
        except AdapterStop as exc:
            issues.append({"status": exc.status, "code": "image_source_anchor_invalid", "message": exc.message})
    return issues


def url_provenance_issues(plan: dict[str, object], data: SliceData) -> list[dict[str, str]]:
    source_slice = plan["source_slice"]
    assert isinstance(source_slice, Path)
    capture_path = source_slice.parent / "source_capture.md"
    metadata_path = source_slice.parent / "source_capture_metadata.json"
    review_packet_path = source_slice.parent / "review_packet.json"
    if not capture_path.exists() or not metadata_path.exists() or not review_packet_path.exists():
        return [{
            "status": "needs_review",
            "code": "missing_url_provenance_packet",
            "message": "verified URL sources require sibling source_capture.md, source_capture_metadata.json, and review_packet.json",
        }]
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8-sig"))
        review_packet = json.loads(review_packet_path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        return [{
            "status": "needs_review",
            "code": "invalid_url_provenance_packet",
            "message": f"cannot read URL provenance packet: {exc}",
        }]
    if not isinstance(metadata, dict) or not isinstance(review_packet, dict):
        return [{
            "status": "needs_review",
            "code": "invalid_url_provenance_packet",
            "message": "URL provenance metadata and review packet must be JSON objects",
        }]
    expected_identity = source_identity(data)
    issues: list[dict[str, str]] = []
    for label, packet, url_key in (
        ("metadata", metadata, "url"),
        ("review packet", review_packet, "source_url"),
    ):
        if packet.get("source_input_kind") != "url":
            issues.append({"status": "needs_review", "code": "url_provenance_kind_mismatch", "message": f"{label} source_input_kind must be url"})
        packet_url = str(packet.get(url_key) or "")
        if canonical_url(packet_url) != canonical_url(data.source_url):
            issues.append({"status": "blocked", "code": "url_provenance_url_mismatch", "message": f"{label} URL does not match the reviewed slice"})
        if packet.get("source_identity") != expected_identity:
            issues.append({"status": "blocked", "code": "url_provenance_identity_mismatch", "message": f"{label} source identity does not match the reviewed slice"})
        if packet.get("source_url_binding_status") != "verified":
            issues.append({"status": "needs_review", "code": "url_provenance_binding_unverified", "message": f"{label} does not confirm a verified URL binding"})
    if metadata.get("capture_provider") == "browser-file":
        try:
            capture_text = capture_path.read_text(encoding="utf-8-sig")
        except OSError as exc:
            return issues + [{"status": "needs_review", "code": "invalid_browser_capture", "message": f"cannot read browser capture: {exc}"}]
        if not source_url_seen_in_text(data.source_url, capture_text):
            issues.append({
                "status": "needs_review",
                "code": "browser_capture_url_not_embedded",
                "message": "browser-file capture must include the source URL or its domain before it can be bound for formal vault write",
            })
    return issues


def source_url_seen_in_text(url: str, text: str) -> bool:
    parsed = urlsplit(url)
    candidates = {url.strip()}
    if parsed.netloc:
        candidates.add(parsed.netloc)
    lowered = text.casefold()
    return any(candidate and candidate.casefold() in lowered for candidate in candidates)


def compounding_metadata_issues(metadata: dict[str, object]) -> list[str]:
    issues: list[str] = []
    status = str(metadata.get("compounding_insight_status") or "")
    types = metadata.get("compounding_insight_types")
    links = metadata.get("compounding_insight_links")
    next_action = str(metadata.get("compounding_next_action") or "")
    section = str(metadata.get("_compounding_section") or "")
    reader_summary = str(metadata.get("_compounding_reader_summary") or "").strip()
    if not section:
        issues.append("missing section 跨來源洞見判斷")
    if status not in VALID_COMPOUNDING_STATUSES:
        issues.append("invalid compounding_insight_status")
    missing_categories = [label for label in VALID_COMPOUNDING_TYPES if f"{label}:" not in section and f"{label}：" not in section]
    if missing_categories:
        issues.append("missing category judgments " + "/".join(missing_categories))
    if has_application_placeholders(section):
        issues.append("section contains draft placeholders")
    residue_terms = application_reader_residue_terms(section)
    if residue_terms:
        issues.append("section contains reader-facing residue " + "/".join(residue_terms))
    type_values = types if isinstance(types, list) else []
    link_values = links if isinstance(links, list) else []
    if status == "has_insight":
        if not reader_summary or has_application_placeholders(reader_summary):
            issues.append("has_insight requires a non-placeholder reader summary under 洞見")
        established = compounding_category_labels(section, "成立")
        if not established:
            issues.append("has_insight requires at least one 成立 category judgment")
        if not type_values:
            issues.append("has_insight requires compounding_insight_types")
        elif set(type_values) != set(established):
            issues.append("has_insight compounding_insight_types must match 成立 category judgments")
        if not link_values:
            issues.append("has_insight requires compounding_insight_links")
        if not next_action or next_action == "暫無":
            issues.append("has_insight requires compounding_next_action")
        cross_source_count = metadata.get("_verified_cross_source_links")
        if isinstance(cross_source_count, int):
            cross_source_issue = has_insight_cross_source_issue(status, cross_source_count)
            if cross_source_issue:
                issues.append(cross_source_issue)
    elif status == "no_compounding_insight":
        if any(compounding_category_decision(section, label) != "不成立" for label in VALID_COMPOUNDING_TYPES):
            issues.append("no_compounding_insight requires every category judgment to be 不成立")
        if type_values:
            issues.append("no_compounding_insight requires empty compounding_insight_types")
        if link_values:
            issues.append("no_compounding_insight requires empty compounding_insight_links")
    elif status == "needs_context":
        if any(compounding_category_decision(section, label) == "成立" for label in VALID_COMPOUNDING_TYPES):
            issues.append("needs_context cannot include a 成立 category judgment")
        if not any(
            compounding_category_decision(section, label) == "需要更多脈絡"
            for label in VALID_COMPOUNDING_TYPES
        ):
            issues.append("needs_context requires at least one 需要更多脈絡 category judgment")
        if type_values:
            issues.append("needs_context requires empty compounding_insight_types")
    if status == "needs_context" and (not next_action or next_action == "暫無"):
        issues.append("needs_context requires compounding_next_action")
    return issues


def compounding_category_decision(section: str, label: str) -> str:
    value = bullet_label(section, label)
    for decision in ("不成立", "需要更多脈絡", "成立"):
        if value == decision or value.startswith(f"{decision}；") or value.startswith(f"{decision}:"):
            return decision
    return ""


def compounding_category_labels(section: str, decision: str) -> list[str]:
    return [
        label
        for label in VALID_COMPOUNDING_TYPES
        if compounding_category_decision(section, label) == decision
    ]


def source_registry_file(vault: Path) -> Path:
    return vault / "_registry" / "sources.jsonl"


def knowledge_point_registry_file(vault: Path) -> Path:
    return vault / "_registry" / "knowledge_points.jsonl"


def relation_registry_file(vault: Path) -> Path:
    return vault / "_registry" / "relations.jsonl"


def canonical_url(value: str) -> str:
    return normalize_url(value)


def source_url_hash(source_url: str) -> str:
    return hashlib.sha256(canonical_url(source_url).encode("utf-8")).hexdigest()


def source_identity(data: SliceData) -> str:
    if data.source_input_kind == "url":
        if not data.source_url:
            raise AdapterStop("needs_review", "missing source URL")
        return f"url:{source_url_hash(data.source_url)}"
    if data.source_input_kind == "user_provided_text":
        if not re.fullmatch(r"[0-9a-f]{64}", data.source_content_sha256):
            raise AdapterStop("needs_review", "user-provided text is missing a valid source_content_sha256")
        return f"user_provided_text:{data.source_content_sha256}"
    if data.source_input_kind == "user_provided_image":
        if not re.fullmatch(r"[0-9a-f]{64}", data.source_image_sha256):
            raise AdapterStop("needs_review", "user-provided image is missing a valid source_image_sha256")
        return f"user_provided_image:{data.source_image_sha256}"
    raise AdapterStop("needs_review", f"unsupported source_input_kind: {data.source_input_kind or '<missing>'}")


def registry_source_identity(row: dict[str, str]) -> str:
    identity = row.get("source_identity", "")
    if identity:
        return identity
    legacy_hash = row.get("source_url_hash", "")
    return f"url:{legacy_hash}" if legacy_hash else ""


def normalized_semantic_text(value: str) -> str:
    lowered = value.casefold()
    compact = re.sub(r"[\W_]+", "", lowered, flags=re.UNICODE)
    return compact or lowered.strip()


def knowledge_point_semantic_key(text: str) -> str:
    return "kp:" + hashlib.sha256(normalized_semantic_text(text).encode("utf-8")).hexdigest()


def knowledge_point_uid(source_id: str, kp_id: str) -> str:
    return f"{source_id}:{kp_id}"


def relation_id(source_id: str, relation: KnowledgeRelation) -> str:
    raw = "|".join([
        source_id,
        relation.source_kp_id,
        relation.target_kp_id,
        normalized_semantic_text(relation.relation),
    ])
    return "rel:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()


def cross_source_relation_id(source_kp_uid: str, target_kp_uid: str, relation: str) -> str:
    """Deterministic identity of one validated cross-source relation (replays write the same row)."""
    raw = "|".join([source_kp_uid, target_kp_uid, relation])
    return "rel:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()


def cross_source_relation_row(*, source_id: str, source_identity: str, source_note_path: str, source_kp_uid: str, source_kp_id: str,
                              target_kp_uid: str, target_kp_id: str, relation: str, evidence: str, confidence: str,
                              target_source_id: str, insight: str, job_id: str, origin: str = "") -> dict[str, str]:
    """Sole assembly of one cross-source relation row (new-source intake and the P4 backfill); ``origin`` is only written when given."""
    row = {
        "relation_id": cross_source_relation_id(source_kp_uid, target_kp_uid, relation),
        "source_id": source_id,
        "source_identity": source_identity,
        "source_note_path": source_note_path,
        "source_kp_uid": source_kp_uid,
        "target_kp_uid": target_kp_uid,
        "source_kp_id": source_kp_id,
        "target_kp_id": target_kp_id,
        "relation": relation,
        "evidence": evidence,
        "confidence": confidence,
        "status": "linked",
        "scope": "cross_source",
        "target_source_id": target_source_id,
        "insight": insight,
        "job_id": job_id,
        "updated": str(date.today()),
    }
    if origin:
        row["origin"] = origin
    return row


def upsert_cross_source_row(existing: dict[str, dict], row: dict) -> str:
    """Put ``row`` into ``existing`` (by relation_id); returns "written", "unchanged" or "withdrawn" (left as it is).

    A withdrawn or superseded row is a review decision that a replay cannot reinstate; unchanged content keeps the
    stored row, date included, so a replay writes nothing new."""
    stored = existing.get(row["relation_id"])
    if stored is not None:
        if stored.get("status", "linked") != "linked":
            return "withdrawn"
        if all(stored.get(key) == value for key, value in row.items() if key != "updated"):
            return "unchanged"
    existing[row["relation_id"]] = row
    return "written"


def registry_rows_text(rows: list[dict]) -> str:
    """Sole text form of a registry JSONL file: one sorted-key JSON object per line, each ending in a line feed."""
    return "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows)


def cross_source_link_id(source_id: str, link: CrossSourceLink) -> str:
    return cross_source_relation_id(knowledge_point_uid(source_id, link.source_kp_id), link.target_kp_uid, link.relation)


def has_insight_cross_source_issue(status: str, verified_cross_source_links: int) -> str:
    """Sole owner of the rule that an established insight needs a verified cross-source relation (new plans only)."""
    if status == "has_insight" and verified_cross_source_links < 1:
        return ("has_insight_without_cross_source: has_insight needs at least one verified cross-source relation from this plan; "
                "use needs_context when no older knowledge point is genuinely linked")
    return ""


def duplicate_candidates(
    registry: dict[str, dict[str, str]],
    data: SliceData,
) -> dict[str, str]:
    semantic_index: dict[str, list[dict[str, str]]] = {}
    for row in registry.values():
        if row.get("status") == "merged":
            continue
        semantic_key = row.get("semantic_key", "")
        if semantic_key:
            semantic_index.setdefault(semantic_key, []).append(row)

    duplicates: dict[str, str] = {}
    for kp in data.knowledge_points:
        semantic_key = knowledge_point_semantic_key(kp.text)
        uid = knowledge_point_uid(data.source_id, kp.kp_id)
        for row in semantic_index.get(semantic_key, []):
            if row.get("kp_uid") != uid:
                duplicates[kp.kp_id] = row.get("kp_uid", "")
                break
    return duplicates


def duplicate_review_report(root: Path, vault: Path) -> dict[str, object]:
    registry = read_knowledge_point_registry(vault)
    candidates = duplicate_candidate_rows(root, registry)
    return {
        "mode": "duplicate_review",
        "status": "ready",
        "write_blocked": False,
        "registry": str(knowledge_point_registry_file(vault).relative_to(root)),
        "candidate_count": len(candidates),
        "candidates": candidates,
    }


def duplicate_candidate_rows(
    root: Path,
    registry: dict[str, dict[str, str]],
) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for row in registry.values():
        if row.get("status") != "duplicate_candidate":
            continue
        if not row.get("duplicate_of"):
            continue
        target = registry.get(row.get("duplicate_of", ""), {})
        rows.append({
            "kp_uid": row.get("kp_uid", ""),
            "duplicate_of": row.get("duplicate_of", ""),
            "kp_id": row.get("kp_id", ""),
            "source_id": row.get("source_id", ""),
            "kp_note_path": row.get("kp_note_path", ""),
            "knowledge_point": row.get("knowledge_point", ""),
            "semantic_key": row.get("semantic_key", ""),
            "target_note_path": target.get("kp_note_path", ""),
            "target_knowledge_point": target.get("knowledge_point", ""),
            "target_status": target.get("status", ""),
        })
    return sorted(rows, key=lambda item: item["kp_uid"])


def resolve_duplicate_review(
    root: Path,
    vault: Path,
    kp_uid: str,
    decision: str | None,
    target_uid: str | None,
    reason: str,
    reviewer: str,
    write: bool,
) -> dict[str, object]:
    if decision is None:
        raise AdapterStop("needs_review", "--duplicate-decision is required when resolving a duplicate")
    registry = read_knowledge_point_registry(vault)
    row = registry.get(kp_uid)
    if row is None:
        raise AdapterStop("needs_review", f"duplicate candidate not found: {kp_uid}")
    if row.get("status") != "duplicate_candidate":
        raise AdapterStop("needs_review", f"kp_uid is not a duplicate_candidate: {kp_uid}")

    resolved_target = target_uid or row.get("duplicate_of", "")
    if decision == "merge":
        if not resolved_target:
            raise AdapterStop("needs_review", "--duplicate-target is required when duplicate_of is empty")
        if resolved_target == kp_uid:
            raise AdapterStop("needs_review", "duplicate target cannot be the same kp_uid")
        if resolved_target not in registry:
            raise AdapterStop("needs_review", f"duplicate target not found: {resolved_target}")
    if decision == "keep":
        resolved_target = ""

    updated = reviewed_duplicate_row(row, decision, resolved_target, reason, reviewer)
    result = {
        "mode": "duplicate_resolve",
        "status": "ready",
        "write_blocked": not write,
        "registry": str(knowledge_point_registry_file(vault).relative_to(root)),
        "kp_uid": kp_uid,
        "decision": decision,
        "duplicate_of": resolved_target,
        "kp_note_path": row.get("kp_note_path", ""),
        "would_write": write,
        "review": {
            "reason": reason,
            "reviewer": reviewer,
            "reviewed_at": str(date.today()),
        },
    }
    if not write:
        return result

    with vault_write_lock(vault):
        # The note goes first, through the one knowledge-point note writer (a completed v2 source's note is registered as a completed-source
        # revision and read back in the same transaction); a refusal or failure there leaves the note and the registry as they were.
        rewritten = rewrite_duplicate_note(root, vault, vault / row.get("kp_note_path", ""), updated, kp_uid)
        registry[kp_uid] = updated
        write_knowledge_point_registry_rows(vault, registry)
    result["write_blocked"] = False
    result["note_written"] = bool(rewritten["written"])
    result["completion_revisions"] = rewritten["revisions"]
    result["decision_receipt"] = rewritten["decision_receipt"]
    result["verification"] = verify_duplicate_resolution(vault, kp_uid, decision, resolved_target)
    verification = result["verification"]
    assert isinstance(verification, dict)
    if not verification["passed"]:
        result["status"] = "needs_review"
    return result


def reviewed_duplicate_row(
    row: dict[str, str],
    decision: str,
    target_uid: str,
    reason: str,
    reviewer: str,
) -> dict[str, str]:
    updated = dict(row)
    updated["status"] = "linked" if decision == "keep" else "merged"
    updated["duplicate_of"] = target_uid
    updated["duplicate_review_decision"] = decision
    updated["duplicate_review_reason"] = reason
    updated["duplicate_reviewed_at"] = str(date.today())
    updated["duplicate_reviewed_by"] = reviewer
    updated["updated"] = str(date.today())
    return updated


def write_knowledge_point_registry_rows(
    vault: Path,
    rows_by_uid: dict[str, dict[str, str]],
) -> None:
    registry_file = knowledge_point_registry_file(vault)
    registry_file.parent.mkdir(parents=True, exist_ok=True)
    rows = [rows_by_uid[key] for key in sorted(rows_by_uid)]
    registry_file.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False, sort_keys=True) for row in rows) + "\n",
        encoding="utf-8",
    )


DUPLICATE_REVIEW_RECEIPT_SCHEMA = "duplicate_review_decision_v1"
DUPLICATE_REVIEW_RECEIPT_DIR = Path("outputs") / "duplicate_review"
DUPLICATE_REVIEW_REASON = "重複複查決定：寫入 duplicate_review_* 欄位與 ## Duplicate Review 小節（不改知識內容）"
DUPLICATE_REVIEW_SUMMARY = ("重複複查只改該知識點筆記 frontmatter 的 duplicate_of、review_status 與 duplicate_review_* 欄位，並增改 ## Duplicate Review 小節，"
                            "不改知識內容；證據是同次寫入前保存、之後不再改寫的複查決定收據。")


def completion_revisions_module():
    """The one writer of knowledge-point notes (imported when needed: it imports this module in turn)."""
    try:
        from tools import completion_revisions as module
    except ModuleNotFoundError:
        import completion_revisions as module  # type: ignore
    return module


def write_duplicate_review_receipt(base: Path, row: dict[str, str], note_path: Path) -> Path:
    """The immutable decision receipt a completed source's revision cites as its evidence: decision, target, reason and reviewer."""
    module = completion_revisions_module()
    moment = datetime.now(timezone.utc)
    receipt = {
        "schema_version": DUPLICATE_REVIEW_RECEIPT_SCHEMA,
        "kp_uid": row.get("kp_uid", ""),
        "decision": row.get("duplicate_review_decision", ""),
        "duplicate_of": row.get("duplicate_of", ""),
        "reason": row.get("duplicate_review_reason", ""),
        "reviewed_by": row.get("duplicate_reviewed_by", ""),
        "reviewed_at": moment.isoformat(timespec="seconds"),
        "kp_note_path": note_path.relative_to(base).as_posix(),
    }
    receipt["payload_sha256"] = module.digest(receipt)
    folder = base / DUPLICATE_REVIEW_RECEIPT_DIR
    folder.mkdir(parents=True, exist_ok=True)
    safe_uid = re.sub(r"[^A-Za-z0-9._-]+", "_", str(row.get("kp_uid", "")))
    path = folder / f"{moment.strftime('%Y%m%dT%H%M%S%fZ')}-{safe_uid}.json"
    with path.open("xb") as stream:  # exclusive: a receipt is never rewritten
        stream.write((json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"))
    return path


def rewrite_duplicate_note(root: Path, vault: Path, note_path: Path, row: dict[str, str], kp_uid: str) -> dict[str, object]:
    """Write the reviewed note through the one note writer.  The note of a completed v2 source needs an accepted revision: the decision
    receipt is written first and is that revision's evidence; any refusal or failure leaves the note as it was."""
    module = completion_revisions_module()
    base = Path(root).resolve()
    if vault.resolve() != managed_vault_path(base):
        raise AdapterStop("needs_review", "selected vault is not this managed workspace's configured vault")
    new_bytes = rendered_duplicate_note(note_path, row)
    try:
        receipt = None
        if any(owner["completed"] for owner in module.classify_kp_notes(base, [note_path])):
            receipt = write_duplicate_review_receipt(base, row, note_path)
        done = module.rewrite_kp_notes(
            base, {note_path: new_bytes}, reason=DUPLICATE_REVIEW_REASON, evidence=receipt, summary=DUPLICATE_REVIEW_SUMMARY,
            authorization=f"使用者執行重複複查 --write（複查人 {row.get('duplicate_reviewed_by', '')}，決定 {row.get('duplicate_review_decision', '')}，{kp_uid}）；"
                          "本修訂由 resolve_duplicate_review 在同一交易內登記，不另行產生授權。",
            note_supports={note_path: f"複查決定收據記錄 {kp_uid} 的決定、目標、理由與複查人；筆記只改 duplicate 相關欄位與 ## Duplicate Review 小節。"})
    except module.CompletedNoteError as exc:
        raise AdapterStop("needs_review", f"{exc} The note and the registry were not changed.") from exc
    return {**done, "decision_receipt": receipt.relative_to(base).as_posix() if receipt else ""}


def rendered_duplicate_note(path: Path, row: dict[str, str]) -> bytes:
    """The note with its duplicate-review fields and section set; every other byte (line endings, BOM) stays."""
    if not path.exists():
        raise AdapterStop("needs_review", f"knowledge-point note missing: {vault_relative(path)}")
    raw = path.read_bytes()
    bom = raw.startswith(b"\xef\xbb\xbf")
    body = raw[3:] if bom else raw
    crlf = b"\r\n" in body
    text = body.decode("utf-8").replace("\r\n", "\n")
    text = upsert_frontmatter_scalars(
        text,
        {
            "duplicate_of": row.get("duplicate_of", ""),
            "review_status": row.get("status", ""),
            "duplicate_review_decision": row.get("duplicate_review_decision", ""),
            "duplicate_review_reason": row.get("duplicate_review_reason", ""),
            "duplicate_reviewed_at": row.get("duplicate_reviewed_at", ""),
            "duplicate_reviewed_by": row.get("duplicate_reviewed_by", ""),
        },
    )
    section = (
        "\n## Duplicate Review\n\n"
        + f"- Decision: {row.get('duplicate_review_decision', '')}\n"
        + f"- Duplicate of: {row.get('duplicate_of', '') or 'not_applicable'}\n"
        + f"- Reason: {row.get('duplicate_review_reason', '') or 'not_recorded'}\n"
        + f"- Reviewed: {row.get('duplicate_reviewed_at', '')} by {row.get('duplicate_reviewed_by', '')}\n"
    )
    text = replace_or_append_h2_section(text, "Duplicate Review", section)
    if crlf:
        text = text.replace("\n", "\r\n")
    return (b"\xef\xbb\xbf" if bom else b"") + text.encode("utf-8")


def upsert_frontmatter_scalars(text: str, updates: dict[str, str]) -> str:
    if not text.startswith("---\n"):
        return text
    end = text.find("\n---", 4)
    if end == -1:
        return text
    frontmatter = text[4:end].splitlines()
    body = text[end:]
    existing_keys: set[str] = set()
    new_lines: list[str] = []
    for line in frontmatter:
        if ":" not in line or line.startswith(" "):
            new_lines.append(line)
            continue
        key, _ = line.split(":", 1)
        key = key.strip()
        if key in updates:
            new_lines.append(f"{key}: {yaml_scalar(updates[key])}")
            existing_keys.add(key)
        else:
            new_lines.append(line)
    for key, value in updates.items():
        if key not in existing_keys:
            new_lines.append(f"{key}: {yaml_scalar(value)}")
    return "---\n" + "\n".join(new_lines) + body


def preserve_existing_created(note: Path, rendered: str) -> str:
    """Keep an existing note's creation date while its generated content is revised."""
    if not note.is_file():
        return rendered
    created = frontmatter_fields(note).get("created")
    return upsert_frontmatter_scalars(rendered, {"created": created}) if isinstance(created, str) and created else rendered


def replace_or_append_h2_section(text: str, title: str, section: str) -> str:
    marker = f"\n## {title}\n"
    start = text.find(marker)
    if start == -1:
        return text.rstrip() + "\n" + section
    next_start = text.find("\n## ", start + len(marker))
    if next_start == -1:
        return text[:start].rstrip() + "\n" + section
    return text[:start].rstrip() + "\n" + section + text[next_start:]


def verify_duplicate_resolution(
    vault: Path,
    kp_uid: str,
    decision: str,
    duplicate_of: str,
) -> dict[str, object]:
    registry = read_knowledge_point_registry(vault)
    row = registry.get(kp_uid, {})
    path = vault / row.get("kp_note_path", "")
    text = path.read_text(encoding="utf-8-sig") if path.exists() else ""
    expected_status = "linked" if decision == "keep" else "merged"
    checks = {
        "registry_status_match": row.get("status") == expected_status,
        "registry_decision_match": row.get("duplicate_review_decision") == decision,
        "registry_duplicate_of_match": row.get("duplicate_of", "") == duplicate_of,
        "note_exists": path.exists(),
        "note_review_status_match": f"review_status: {yaml_scalar(expected_status)}" in text,
        "note_decision_match": f"duplicate_review_decision: {yaml_scalar(decision)}" in text,
    }
    return {
        "passed": all(checks.values()),
        "checks": checks,
    }


def relations_grouped_by_source(relations: list[KnowledgeRelation]) -> dict[str, list[KnowledgeRelation]]:
    grouped: dict[str, list[KnowledgeRelation]] = {}
    for relation in relations:
        grouped.setdefault(relation.source_kp_id, []).append(relation)
    return grouped


def project_active_relation_markdown(text: str, relations: list[KnowledgeRelation]) -> str:
    """Filter only generated relation rows; retained original prose is immutable."""
    canonical_span = canonical_source_section_span(text)
    offset = canonical_span[1] if canonical_span else 0
    suffix = text[offset:]
    match = re.search(r"(?ms)^### 關係草案\n.*?(?=^## |^### |\Z)", suffix)
    if not match: return text
    allowed = {(r.source_kp_id, r.target_kp_id, normalized_semantic_text(r.relation)) for r in relations}
    lines = []
    for line in match.group().splitlines(keepends=True):
        cells = [clean_table_cell(cell) for cell in split_markdown_table_cells(line)] if line.startswith("|") else []
        if len(cells) == 5 and cells[0].startswith("KP-") and cells[1].startswith("KP-"):
            if (cells[0], cells[1], normalized_semantic_text(cells[2])) not in allowed: continue
        lines.append(line)
    return text[:offset] + suffix[:match.start()] + ''.join(lines) + suffix[match.end():]


def read_source_registry(vault: Path) -> dict[str, dict[str, str]]:
    rows = read_source_registry_rows(vault)
    entries: dict[str, dict[str, str]] = {}
    for line_no, item in rows:
        key = registry_source_identity(item)
        if not key:
            raise AdapterStop(
                "needs_review",
                f"registry row missing source_identity at {source_registry_file(vault)}:{line_no}",
            )
        if key in entries:
            raise AdapterStop(
                "needs_review",
                f"duplicate source_identity at {source_registry_file(vault)}:{line_no}",
            )
        entries[key] = item
    return entries


def read_source_registry_rows(vault: Path) -> list[tuple[int, dict[str, str]]]:
    path = source_registry_file(vault)
    if not path.exists():
        return []
    rows: list[tuple[int, dict[str, str]]] = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError as exc:
            raise AdapterStop("needs_review", f"invalid registry JSON at {path}:{line_no}: {exc}") from exc
        rows.append((line_no, {str(key): str(value) for key, value in item.items()}))
    return rows


def read_knowledge_point_registry(vault: Path) -> dict[str, dict[str, str]]:
    rows = read_jsonl_rows(knowledge_point_registry_file(vault))
    entries: dict[str, dict[str, str]] = {}
    for line_no, item in rows:
        key = str(item.get("kp_uid", ""))
        if not key:
            raise AdapterStop(
                "needs_review",
                f"knowledge-point registry row missing kp_uid at {knowledge_point_registry_file(vault)}:{line_no}",
            )
        if key in entries:
            raise AdapterStop(
                "needs_review",
                f"duplicate kp_uid at {knowledge_point_registry_file(vault)}:{line_no}",
            )
        entries[key] = item
    return entries


def read_relation_registry(vault: Path) -> dict[str, dict[str, str]]:
    rows = read_jsonl_rows(relation_registry_file(vault))
    entries: dict[str, dict[str, str]] = {}
    for line_no, item in rows:
        key = str(item.get("relation_id", ""))
        if not key:
            raise AdapterStop(
                "needs_review",
                f"relation registry row missing relation_id at {relation_registry_file(vault)}:{line_no}",
            )
        if key in entries:
            raise AdapterStop(
                "needs_review",
                f"duplicate relation_id at {relation_registry_file(vault)}:{line_no}",
            )
        entries[key] = item
    return entries


def resolved_kp_relations(vault: Path, fields: dict, body: str, labels: dict[str, str] | None = None) -> tuple[dict, str]:
    """Use the relation registry for a saved KP's reader projection.

    ``labels`` maps a relation word to the text shown for it; a relation without an entry (a legacy free string)
    keeps its own text.  The note refresh passes none, so the notes written into the vault do not change.
    """
    rows = [row for row in read_relation_registry(vault).values()
            if row.get('source_kp_uid') == fields.get('kp_uid')]
    if not rows:
        return fields, body
    active = [row for row in rows if row.get('status', 'linked') == 'linked']
    fields = {**fields, 'related_points': sorted({row['target_kp_id'] for row in active})}
    lines = [f"- {row['target_kp_id']}：{(labels or {}).get(row['relation'], row['relation'])}；證據：{row['evidence']}" for row in active]
    if not active:
        lines = ['- 暫無已核對的直接關係；待覆核記錄保留於 registry，不作已成立的來源主張。']
    replacement = '## Registered Relations\n\n'+'\n'.join(lines)+'\n\n'
    body = re.sub(r'(?ms)^## Registered Relations\n.*?(?=^## |\Z)', lambda _:replacement, body)
    return fields, body


def apply_reviewed_note_revision(vault: Path, target: Path, candidate: bytes, *, before_sha256: str, accepted_sha256: str) -> None:
    """Apply an explicitly reviewed same-path note with compare-and-swap checks."""
    assert_management_idle_for_vault(vault)
    vault = vault.resolve()
    if not target.resolve().is_relative_to(vault):
        raise ValueError('reviewed note is outside vault')
    for component in (target, *target.parents):
        if component == vault: break
        if component.is_symlink() or (hasattr(component, 'is_junction') and component.is_junction()):
            raise ValueError('reviewed note path contains a link')
    raw = target.read_bytes()
    if hashlib.sha256(raw).hexdigest() != before_sha256 or hashlib.sha256(candidate).hexdigest() != accepted_sha256:
        raise ValueError('reviewed note changed since review')
    if target.suffix != '.md' or frontmatter_fields(target).get('type') not in {'source', 'application'}:
        raise ValueError('reviewed note type is not supported')
    before = extract_canonical_body_markdown(raw.decode('utf-8-sig').replace('\r\n','\n'))
    after = extract_canonical_body_markdown(candidate.decode('utf-8-sig').replace('\r\n','\n'))
    if before != after:
        raise ValueError('reviewed note cannot change original body')
    current_fields = frontmatter_fields(target)
    note_mode = current_fields.get('source_retention_mode')
    if current_fields.get('type') == 'application':
        # An application note takes its retention mode from its source's registry row.
        note_mode = next((row.get('source_retention_mode') for row in read_source_registry(vault).values()
                          if row.get('source_id') == current_fields.get('source_id')), None)
    if note_mode == TRANSCRIPT_ARCHIVE_MODE and has_cue_timing_line(candidate.decode('utf-8-sig')):
        raise ValueError('reviewed note would put a caption cue timing line into the Vault; the transcript stays in the package archive')
    target.write_bytes(candidate)
    if target.read_bytes() != candidate:
        raise ValueError('reviewed note write readback failed')


def refresh_registered_relation_notes(root: Path, vault: Path, source_id: str, *, kp_ids: set[str] | None = None) -> list[Path]:
    """Update only adapter-owned relation fields/sections, retaining evidence text."""
    assert_management_idle_for_vault(vault)
    changes: dict[Path, bytes] = {}
    for path in sorted((vault/'knowledge-points/by-source'/source_id).glob('*.md')):
        text = path.read_text(encoding='utf-8-sig')
        fields = frontmatter_fields(path)
        if fields.get('type') != 'knowledge_point': continue
        if kp_ids is not None and str(fields.get('kp_id') or path.stem) not in kp_ids: continue
        _, header, body = text.split('---', 2)
        updated, new_body = resolved_kp_relations(vault, fields, body)
        if updated == fields and new_body == body: continue
        lines = 'related_points:' + ('\n'+''.join('  - '+json.dumps(value,ensure_ascii=False)+'\n' for value in updated['related_points']) if updated['related_points'] else ' []\n')
        header = re.sub(r'(?m)^related_points:[^\n]*\n(?:  -[^\n]*\n)*', lambda _:lines, header)
        candidate = '---'+header+'---'+new_body
        if candidate != text:
            changes[path] = candidate.encode('utf-8')
    if changes:
        # Through the one note writer: a note of a completed v2 source has no evidence here, so the writer refuses it.
        module = completion_revisions_module()
        try:
            base = Path(root).resolve()
            if vault.resolve() != managed_vault_path(base):
                raise AdapterStop('needs_review', "selected vault is not this managed workspace's configured vault")
            module.rewrite_kp_notes(base, changes, reason='registered relation fields and section refreshed from the relation registry')
        except module.CompletedNoteError as exc:
            raise AdapterStop('needs_review', f'{exc} No note was changed.') from exc
    return list(changes)


def read_jsonl_rows(path: Path) -> list[tuple[int, dict[str, str]]]:
    if not path.exists():
        return []
    rows: list[tuple[int, dict[str, str]]] = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError as exc:
            raise AdapterStop("needs_review", f"invalid JSONL at {path}:{line_no}: {exc}") from exc
        rows.append((line_no, {str(key): str(value) for key, value in item.items()}))
    return rows


def resolve_source_registry(vault: Path, data: SliceData) -> SourceRegistryResolution:
    key = source_identity(data)
    registry = read_source_registry(vault)
    if key in registry:
        row = registry[key]
        registered_path = vault / str(row.get("source_note_path", ""))
        issue = validate_registered_source_note(registered_path, data, row)
        mode = (
            "registry_legacy"
            if is_legacy_source_note(vault, registered_path)
            or is_noncanonical_registered_source_note(vault, registered_path, data)
            else "registry"
        )
        source_note = sharded_source_note(vault, data) if mode == "registry_legacy" else registered_path
        return SourceRegistryResolution(key, source_note, mode, issue)

    legacy_note = existing_source_note(vault, data.source_url) if data.source_input_kind == "url" else None
    if legacy_note:
        return SourceRegistryResolution(key, sharded_source_note(vault, data), "legacy_bootstrap")

    return SourceRegistryResolution(key, sharded_source_note(vault, data), "new_sharded")


def validate_registered_source_note(
    path: Path, data: SliceData, registered: dict[str, str] | None = None,
) -> str | None:
    if not path.exists():
        return f"needs_review: registry points to missing source note: {vault_relative(path)}"
    text = path.read_text(encoding="utf-8-sig")
    if f"source_input_kind: {yaml_scalar(data.source_input_kind)}" not in text and data.source_input_kind != "url":
        return f"needs_review: registry source_input_kind mismatch for {vault_relative(path)}"
    is_legacy_url_note = data.source_input_kind == "url" and "source_identity:" not in text
    if not is_legacy_url_note and f"source_identity: {yaml_scalar(source_identity(data))}" not in text:
        return f"needs_review: registry source_identity mismatch for {vault_relative(path)}"
    if data.source_input_kind == "url" and f"source_url: {yaml_scalar(data.source_url)}" not in text:
        return f"needs_review: registry source_url mismatch for {vault_relative(path)}"
    if data.source_input_kind == "user_provided_text" and f"source_content_sha256: {yaml_scalar(data.source_content_sha256)}" not in text:
        return f"needs_review: registry source_content_sha256 mismatch for {vault_relative(path)}"
    if data.source_input_kind == "user_provided_image" and f"source_image_sha256: {yaml_scalar(data.source_image_sha256)}" not in text:
        return f"needs_review: registry source_image_sha256 mismatch for {vault_relative(path)}"
    if f"source_id: {yaml_scalar(data.source_id)}" not in text:
        return f"needs_review: registry source_id mismatch for {vault_relative(path)}"
    if registered and registered.get("source_retention_mode") == "full_body_required":
        expected_hash = registered.get("canonical_body_sha256", "")
        body = extract_canonical_body_markdown(text)
        fields = frontmatter_fields(path)
        if (
            not expected_hash or not body
            or canonical_body_fingerprint(body) != expected_hash
            or fields.get("canonical_body_sha256") != expected_hash
            or fields.get("source_retention_mode") != "full_body_required"
        ):
            return f"needs_review: registered canonical body mismatch for {vault_relative(path)}; preserve the changed note and reconcile against the accepted source before retrying"
    if registered and registered.get("source_retention_mode") == TRANSCRIPT_ARCHIVE_MODE:
        expected_hash = registered.get("canonical_body_sha256", "")
        fields = frontmatter_fields(path)
        if (
            not expected_hash
            or fields.get("canonical_body_sha256") != expected_hash
            or fields.get("source_retention_mode") != TRANSCRIPT_ARCHIVE_MODE
            or has_cue_timing_line(text)
        ):
            return f"needs_review: registered transcript archive declaration mismatch for {vault_relative(path)}; preserve the changed note and reconcile against the package archive before retrying"
    return None


def is_legacy_source_note(vault: Path, path: Path) -> bool:
    try:
        relative = path.relative_to(vault)
    except ValueError:
        return False
    parts = relative.parts
    return len(parts) == 2 and parts[0] == "sources"


def is_noncanonical_registered_source_note(vault: Path, path: Path, data: SliceData) -> bool:
    if not path.exists():
        return False
    try:
        relative = path.relative_to(vault)
    except ValueError:
        return False
    parts = relative.parts
    if len(parts) != 4 or parts[0] != "sources":
        return False
    canonical = sharded_source_note(vault, data)
    return path != canonical and path.name.startswith(f"{data.source_id}_")


def sharded_source_note(vault: Path, data: SliceData) -> Path:
    year, month = year_month(data.capture_date)
    return vault / "sources" / year / month / f"{data.source_id}_{data.slug}.md"


def sharded_application_note(vault: Path, data: SliceData) -> Path:
    year, month = year_month(data.capture_date)
    return vault / "applications" / year / month / f"APP-{data.source_id}_{data.slug}.md"


def year_month(value: str) -> tuple[str, str]:
    match = re.match(r"(\d{4})-(\d{2})", value.strip())
    if match:
        return match.group(1), match.group(2)
    today = date.today()
    return str(today.year), f"{today.month:02d}"


def existing_source_note(vault: Path, source_url: str) -> Path | None:
    sources = vault / "sources"
    if not sources.exists():
        return None
    needle = f"source_url: {yaml_scalar(source_url)}"
    for path in sorted(sources.rglob("*.md")):
        if needle in path.read_text(encoding="utf-8-sig"):
            return path
    return None


def existing_knowledge_point_note(vault: Path, kp_id: str) -> Path | None:
    root = vault / "knowledge-points"
    if not root.exists():
        return None
    for path in sorted(root.rglob(f"{kp_id}.md")):
        return path
    return None


def existing_application_note(vault: Path, data: SliceData) -> Path | None:
    root = vault / "applications"
    if not root.exists():
        return None
    pattern = f"APP-{data.source_id}_{data.slug}.md"
    for path in sorted(root.rglob(pattern)):
        return path
    return None


def render_source_note(
    data: SliceData,
    source_slice: Path,
    source_note: Path,
    asset_dests: list[Path],
    case_asset_srcs: list[Path],
    case_asset_dests: list[Path],
    source_image_dest: Path | None,
) -> str:
    kp_records = [kp.kp_id for kp in data.knowledge_points]
    asset_rels = [vault_relative(asset_dest) for asset_dest in asset_dests]
    case_asset_rels = [vault_relative(asset_dest) for asset_dest in case_asset_dests]
    case_image_rels = [
        vault_relative(destination)
        for source, destination in zip(case_asset_srcs, case_asset_dests)
        if source in data.case_evidence_image_paths
    ]
    source_image_rel = vault_relative(source_image_dest) if source_image_dest else None
    identity = source_identity(data)
    frontmatter = {
        "type": "source",
        "source_id": data.source_id,
        "source_input_kind": data.source_input_kind,
        "source_identity": identity,
        "asset_mode": data.asset_mode,
        "infographic_status": data.infographic_status,
        "source_retention_mode": data.source_retention_mode,
        "title": data.title,
        "source_type": data.source_type,
        "capture_date": data.capture_date,
        "readability_status": data.readability_status,
        "ai_source_analysis_status": data.ai_source_analysis_status,
        "ai_source_analysis_evidence": sanitize_source_note_frontmatter_text(data.ai_source_analysis_evidence),
        "png_generation_method": data.png_generation_method,
        "png_semantic_validation_status": data.png_semantic_validation_status,
        "png_semantic_validation_evidence": sanitize_source_note_frontmatter_text(data.png_semantic_validation_evidence),
        "png_reverse_review_status": data.png_reverse_review_status,
        "png_reverse_review_evidence": sanitize_source_note_frontmatter_text(data.png_reverse_review_evidence),
        "infographic_importance_coverage": sanitize_source_note_frontmatter_text(data.infographic_importance_coverage),
        "infographic_count_decision": sanitize_source_note_frontmatter_text(data.infographic_count_decision),
        "coverage_audit_status": data.coverage_audit_status,
        "coverage_unit_total": data.coverage_unit_total,
        "coverage_unit_infographic_mapped": data.coverage_unit_infographic_mapped,
        "coverage_unit_omitted": data.coverage_unit_omitted,
        "coverage_unit_summary_only": data.coverage_unit_summary_only,
        "infographic_page_count": data.infographic_page_count,
        "infographic_multi_volume_status": data.infographic_multi_volume_status,
        "knowledge_use": data.knowledge_use,
        "source_confidence": data.source_confidence,
        "summary": data.summary,
        "status": "linked",
        "fact_network_status": "建議入網",
        "source_assets": ([source_image_rel] if source_image_rel else []) + asset_rels + case_asset_rels,
        "infographics": asset_rels if data.asset_mode == ASSET_MODE_FORMAL else [],
        "case_evidence_assets": case_asset_rels,
        "evidence_screenshots": case_image_rels,
        "knowledge_points": kp_records,
        "registry_path": "_registry/sources.jsonl",
        "created": str(date.today()),
        "updated": str(date.today()),
    }
    if data.retrieval_aliases:
        frontmatter["retrieval_aliases"] = data.retrieval_aliases
    if data.source_input_kind == "url":
        frontmatter["source_url"] = data.source_url
        frontmatter["source_url_hash"] = source_url_hash(data.source_url)
    elif data.source_input_kind == "user_provided_text":
        frontmatter["source_content_sha256"] = data.source_content_sha256
    else:
        frontmatter["source_image_sha256"] = data.source_image_sha256
        frontmatter["source_image_mime"] = data.source_image_mime
        frontmatter["source_image_width"] = data.source_image_width
        frontmatter["source_image_height"] = data.source_image_height
        frontmatter["source_original_image"] = source_image_rel or ""
    if data.source_retention_mode in CANONICAL_HASH_RETENTION_MODES:
        frontmatter["canonical_body_sha256"] = data.canonical_body_sha256
    if data.asset_mode == ASSET_MODE_CASE:
        frontmatter["case_evidence_manifest"] = case_asset_rels[0] if case_asset_rels else ""
        frontmatter["case_evidence_manifest_sha256"] = data.case_evidence_manifest_sha256
    kp_list = "\n".join(f"- `{record}`" for record in kp_records)
    body = (
        rewrite_case_evidence_assets(data.source_markdown, case_asset_srcs, case_asset_dests)
        if data.asset_mode == ASSET_MODE_CASE
        else rewrite_infographic_embeds(data.source_markdown, data, asset_dests)
    )
    return (
        render_frontmatter(frontmatter)
        + f"# {data.title}\n\n"
        + f"> Vault note generated from `{source_slice.as_posix()}` by `tools/obsidian_vault_adapter.py`.\n\n"
        + "## Vault Links\n\n"
        + f"- Source note path: `{vault_relative(source_note)}`\n"
        + f"- Source identity: `{identity}`\n"
        + (f"- Source URL hash: `{source_url_hash(data.source_url)}`\n" if data.source_input_kind == "url" else "")
        + (
            f"- Original image: `{source_image_rel}`\n"
            f"- Original image SHA-256: `{data.source_image_sha256}`\n"
            f"- Original image dimensions: `{data.source_image_width} × {data.source_image_height}`\n"
            if data.source_input_kind == "user_provided_image" and source_image_rel
            else ""
        )
        + "- Registry: `_registry/sources.jsonl`\n"
        + (
            "".join(f"- Infographic asset: `{asset}`\n" for asset in asset_rels)
            if data.asset_mode == ASSET_MODE_FORMAL and asset_rels
            else "".join(f"- Case evidence asset: `{asset}`\n" for asset in case_asset_rels)
            if data.asset_mode == ASSET_MODE_CASE and case_asset_rels
            else ""
            if data.asset_mode == ASSET_MODE_KNOWLEDGE
            else "- Asset: not_available\n"
        )
        + "- Knowledge points:\n"
        + kp_list
        + "\n\n---\n\n"
        + (f"## 原始圖片證據\n\n![[{source_image_rel}]]\n\n" if source_image_rel else "")
        + body
        + "\n"
    )


def render_kp_note(
    kp: KnowledgePoint,
    source_note: Path,
    duplicate_of: str,
    outgoing_relations: list[KnowledgeRelation],
) -> str:
    source_record = source_note.stem
    related_records = [relation.target_kp_id for relation in outgoing_relations]
    frontmatter = {
        "type": "knowledge_point",
        "kp_id": kp.kp_id,
        "kp_uid": knowledge_point_uid(vault_source_id_from_note(source_note), kp.kp_id),
        "semantic_key": knowledge_point_semantic_key(kp.text),
        "semantic_text": normalized_semantic_text(kp.text),
        "knowledge_point": kp.text,
        "knowledge_title": kp.title,
        "reusable_concept_status": kp.reusable_concept_status,
        "reusable_concept_name": kp.reusable_concept_name,
        "reusable_concept_reason": kp.reusable_concept_reason,
        "definition": kp.text,
        "knowledge_type": kp.kind,
        "source_note": source_record,
        "source_anchor": kp.evidence,
        "confidence": kp.confidence,
        "applicable_scenes": [],
        "usable_for": [],
        "related_points": related_records,
        "duplicate_of": duplicate_of,
        "review_status": "duplicate_candidate" if duplicate_of else "自動建議",
        "registry_path": "_registry/knowledge_points.jsonl",
    }
    relation_lines = "\n".join(
        f"- {relation.relation}: `{relation.target_kp_id}` ({relation.confidence}; {relation.evidence})"
        for relation in outgoing_relations
    ) or "- 暫無已登記關係。"
    return (
        render_frontmatter(frontmatter)
        + f"# {kp.kp_id} — {kp.text}\n\n"
        + f"- Source record: `{source_record}`\n"
        + f"- Evidence anchor: {kp.evidence}\n"
        + f"- Confidence: {kp.confidence}\n\n"
        + "## Definition\n\n"
        + kp.text
        + "\n\n## Registered Relations\n\n"
        + relation_lines
        + "\n"
    )


def vault_source_id_from_note(source_note: Path) -> str:
    name = source_note.stem
    return name.split("_", 1)[0] if "_" in name else name


def render_application_note(
    data: SliceData,
    source_note: Path,
    application_note: Path,
    asset_dest: Path | None,
) -> str:
    source_record = source_note.stem
    metadata = application_metadata(data)
    metadata_frontmatter = {key: value for key, value in metadata.items() if not key.startswith("_")}
    taste_network_status = "不入網" if data.taste_network_status == "否" else data.taste_network_status
    asset_rel = vault_relative(asset_dest) if asset_dest else ""
    source_rel = vault_relative(source_note)
    application_rel = vault_relative(application_note)
    knowledge_points_rel = f"knowledge-points/by-source/{data.source_id}"
    ingest_status = "完整" if data.asset_mode == ASSET_MODE_KNOWLEDGE else "完整" if asset_rel else "需檢查"
    issue_reason = "無" if data.asset_mode == ASSET_MODE_KNOWLEDGE else "無" if asset_rel else "缺少資訊圖"
    next_action = overview_next_action(
        metadata["ai_adoption_status"],
        metadata["taste_link_status"],
        data.asset_mode == ASSET_MODE_KNOWLEDGE or bool(asset_rel),
        has_infographic=bool(asset_rel),
    )
    ai_confidence = ai_review_confidence(data, metadata)
    overview_fields = {
        "項目": concise_overview_title(data.title),
        "入庫": ingest_status,
        "AI總判斷": metadata["ai_adoption_status"],
        "AI信心": ai_confidence,
        "下一步": next_action,
        "複利": compounding_label(str(metadata["compounding_insight_status"])),
        "複利下一步": metadata["compounding_next_action"],
        "品味": taste_network_status,
        "扣連": taste_link_label(metadata["taste_link_status"]),
        "問題": issue_reason,
        "修正": DEFAULT_MANUAL_CORRECTION_STATUS,
        "PNG": asset_rel,
        "原文": source_rel,
        "應用": application_rel,
        "知識點": knowledge_points_rel,
    }
    frontmatter = {
        "type": "application",
        "application_id": f"APP-{data.source_id}",
        "title": f"{data.title} — 個人應用",
        "source_note": source_record,
        "source_id": data.source_id,
        "ingest_status": ingest_status,
        "next_action": next_action,
        "issue_reason": issue_reason,
        "png_entry": asset_rel,
        "source_entry": source_rel,
        "application_entry": application_rel,
        "knowledge_points_entry": knowledge_points_rel,
        **overview_fields,
        "personal_relevance": metadata["personal_relevance"],
        "taste_network_status": taste_network_status,
        "action_status": metadata["action_status"],
        "adoption_status": metadata["ai_adoption_status"],
        "adoption_reason": metadata["ai_adoption_reason"],
        "manual_correction_status": DEFAULT_MANUAL_CORRECTION_STATUS,
        "manual_correction_action": DEFAULT_MANUAL_CORRECTION_ACTION,
        **metadata_frontmatter,
        "ai_review_confidence": ai_confidence,
        "taste_network_reason": data.taste_network_reason,
        "human_verification_items": DEFAULT_HUMAN_VERIFICATION_ITEMS,
        "life_scenes": [],
        "application_status": "active",
        "created": str(date.today()),
        "updated": str(date.today()),
    }
    body = data.application_markdown or "## 個人應用摘要\n\n暫無個人應用摘要。"
    body = re.sub(r"^<!-- related_sources_json: .+ -->\n?", "", body, flags=re.M)
    body = re.sub(r"^<!-- application_semantics_json: .+ -->\n?", "", body, flags=re.M)
    return (
        render_frontmatter(frontmatter)
        + f"# {data.title} — 個人應用\n\n"
        + f"> 本筆記獨立於 source note,只保存 使用者 應用層,不作 URL 原文內容真源。\n\n"
        + f"- Source record: `{source_record}`\n"
        + f"- Application note path: `{vault_relative(application_note)}`\n\n"
        + "## AI 採納判斷\n\n"
        + f"- 狀態:{metadata['ai_adoption_status']}\n"
        + f"- 一句原因:{metadata['ai_adoption_reason']}\n"
        + "- 原則:此判斷已生效;使用者 只在不同意時手動修正 status。\n\n"
        + "## 人工修正\n\n"
        + f"- 狀態:{DEFAULT_MANUAL_CORRECTION_STATUS}\n"
        + f"- 可做動作:{DEFAULT_MANUAL_CORRECTION_ACTION}\n\n"
        + "## 品味網判斷\n\n"
        + f"- 狀態:{taste_network_status}\n"
        + f"- 原因:{data.taste_network_reason}\n\n"
        + "## 個人化扣連狀態\n\n"
        + f"- 狀態:{metadata['personalization_status']}\n"
        + "".join(f"- 扣連:{item}\n" for item in metadata["personalization_links"])
        + "\n## 品味扣連狀態\n\n"
        + f"- 狀態:{metadata['taste_link_status']}\n"
        + "".join(f"- 扣連:{item}\n" for item in metadata["taste_links"])
        + "\n## 跨來源洞見判斷\n\n"
        + f"- 狀態:{metadata['compounding_insight_status']}\n"
        + "".join(f"- 類型:{item}\n" for item in metadata["compounding_insight_types"])
        + "".join(f"- 扣連:{item}\n" for item in metadata["compounding_insight_links"])
        + f"- 下一步:{metadata['compounding_next_action']}\n"
        + compounding_category_lines(str(metadata.get("_compounding_section") or ""))
        + "\n"
        + "## 仍需人工驗證事項\n\n"
        + "".join(f"- {item}\n" for item in DEFAULT_HUMAN_VERIFICATION_ITEMS)
        + "\n"
        + body
        + "\n"
    )


def ai_review_confidence(data: SliceData, metadata: dict[str, object]) -> str:
    if (
        data.ai_source_analysis_status == "complete"
        and data.png_semantic_validation_status == "passed"
        and metadata.get("ai_adoption_status") not in {"", DEFAULT_AI_ADOPTION_STATUS}
        and metadata.get("ai_adoption_reason") not in {"", DEFAULT_AI_ADOPTION_REASON}
    ):
        return "高"
    if data.ai_source_analysis_status == "complete":
        return "中"
    return "低"


def compounding_label(status: str) -> str:
    return {
        "has_insight": "有洞見",
        "no_compounding_insight": "暫無洞見",
        "needs_context": "需脈絡",
    }.get(status, "需檢查")


def compounding_category_lines(section: str) -> str:
    lines: list[str] = []
    for label in VALID_COMPOUNDING_TYPES:
        value = bullet_label(section, label)
        if not value:
            value = "需要更多脈絡。"
        lines.append(f"- {label}:{value}\n")
    return "".join(lines)


def render_frontmatter(values: dict[str, object]) -> str:
    lines = ["---"]
    for key, value in values.items():
        if isinstance(value, list):
            if value:
                lines.append(f"{key}:")
                for item in value:
                    lines.append(f"  - {yaml_scalar(str(item))}")
            else:
                lines.append(f"{key}: []")
        else:
            lines.append(f"{key}: {yaml_scalar(str(value))}")
    lines.append("---")
    return "\n".join(lines) + "\n\n"


def _reuse_trial_path(root: Path, value: object, label: str) -> tuple[Path, str]:
    raw = str(value or "").strip().replace("\\", "/")
    if not raw:
        raise AdapterStop("needs_review", f"reuse trial {label} is missing")
    candidate = (root / raw).resolve()
    if not candidate.is_file() or not candidate.is_relative_to(root.resolve()):
        raise AdapterStop("needs_review", f"reuse trial {label} must be an existing file inside this workspace")
    return candidate, candidate.relative_to(root.resolve()).as_posix()


def _reuse_trial_slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")
    if not slug:
        raise AdapterStop("needs_review", "reuse trial id cannot form a safe application filename")
    return slug[:60]


def _reuse_markdown_link(from_path: Path, destination: Path, label: str) -> str:
    return f"[{label}]({Path(os.path.relpath(destination, start=from_path.parent)).as_posix()})"


def _reuse_assessments(root: Path, value: object) -> list[dict[str, object]]:
    if not isinstance(value, list) or not value:
        raise AdapterStop("needs_review", "reuse trial assessments is required")
    statuses = {"verified", "insufficient", "unverified", "not_applicable"}
    assessments: list[dict[str, object]] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, dict):
            raise AdapterStop("needs_review", "reuse trial assessment must be an object")
        dimension = str(item.get("dimension") or "").strip()
        status = str(item.get("status") or "").strip()
        summary = str(item.get("summary") or "").strip()
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]*", dimension) or dimension in seen or status not in statuses or not summary:
            raise AdapterStop("needs_review", "reuse trial assessment needs a unique dimension, supported status and summary")
        raw_paths = item.get("evidence_paths")
        if not isinstance(raw_paths, list) or not raw_paths:
            raise AdapterStop("needs_review", "reuse trial assessment requires evidence_paths")
        evidence = [_reuse_trial_path(root, path, "assessment evidence_paths")[1] for path in raw_paths]
        assessments.append({"dimension": dimension, "status": status, "summary": summary, "evidence_paths": evidence})
        seen.add(dimension)
    return assessments


def write_research_reuse_application(
    *,
    root: Path,
    vault: Path,
    source_id: str,
    source_identity: str,
    topic_id: str,
    trial: dict[str, object],
) -> dict[str, str]:
    """Write one separate reuse record without changing receipt-bound records.

    This deliberately does not update source/KP registries, the original APP,
    assets, or any Compiler receipt.  It is only a derived application note
    linked to an already-verified source identity.
    """
    root = root.resolve()
    vault = vault.resolve()
    try:
        configured_vault = vault_path(root)
    except WorkspacePathError as exc:
        raise AdapterStop("blocked", str(exc)) from exc
    if vault != configured_vault:
        raise AdapterStop("blocked", "reuse application vault must be this workspace's configured vault")
    if not re.fullmatch(r"KS-[A-Za-z0-9.-]+", source_id):
        raise AdapterStop("needs_review", "reuse application source_id is invalid")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", topic_id):
        raise AdapterStop("needs_review", "reuse application topic_id is invalid")
    trial_id = str(trial.get("trial_id") or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", trial_id):
        raise AdapterStop("needs_review", "reuse trial_id is invalid")
    title = str(trial.get("title") or "").strip()
    lesson = str(trial.get("lesson") or "").strip()
    reuse_method = str(trial.get("reuse_method") or "").strip()
    limitations = [str(item).strip() for item in trial.get("limitations", []) if str(item).strip()] if isinstance(trial.get("limitations"), list) else []
    if not title or not lesson or not reuse_method or not limitations:
        raise AdapterStop("needs_review", "reuse trial must state method, lesson and limitations")
    assessments = _reuse_assessments(root, trial.get("assessments"))
    work_source, original_work_path = _reuse_trial_path(root, trial.get("work_path"), "work_path")
    if work_source.suffix.casefold() not in {".html", ".htm"}:
        raise AdapterStop("needs_review", "reuse trial work_path must be a self-contained HTML work")
    html_links = re.findall(r"(?:src|href)\s*=\s*['\"]([^'\"]+)['\"]", work_source.read_text(encoding="utf-8", errors="replace"), flags=re.IGNORECASE)
    if any(link and not link.startswith(("#", "data:", "http://", "https://", "mailto:")) for link in html_links):
        raise AdapterStop("needs_review", "reuse trial HTML must be self-contained; local linked dependencies are not supported by this narrow writer")
    raw_evidence = trial.get("evidence_paths")
    if not isinstance(raw_evidence, list) or not raw_evidence:
        raise AdapterStop("needs_review", "reuse trial evidence_paths is required")
    evidence_sources = [_reuse_trial_path(root, item, "evidence_paths") for item in raw_evidence]
    original_evidence_paths = [path for _, path in evidence_sources]
    if original_work_path not in original_evidence_paths:
        original_evidence_paths.insert(0, original_work_path)
    for assessment in assessments:
        for path in assessment["evidence_paths"]:
            if path not in original_evidence_paths:
                _reuse_trial_path(root, path, "assessment evidence_paths")
                original_evidence_paths.append(path)

    registry_rows = [row for _, row in read_source_registry_rows(vault) if row.get("source_id") == source_id]
    if len(registry_rows) != 1 or registry_rows[0].get("source_identity") != source_identity:
        raise AdapterStop("needs_review", "reuse application source identity is not uniquely registered")
    source_entry = str(registry_rows[0].get("source_note_path") or "")
    source_note = (vault / source_entry).resolve()
    if not source_entry or not source_note.is_file() or not source_note.is_relative_to(vault):
        raise AdapterStop("needs_review", "reuse application registered source note is missing or outside vault")
    try:
        from tools.outcome_page_generator import resolve_application_note
    except ModuleNotFoundError:
        from outcome_page_generator import resolve_application_note  # type: ignore
    original_application = resolve_application_note(vault, source_id, source_note)
    if not original_application.is_file() or not original_application.is_relative_to(vault):
        raise AdapterStop("needs_review", "reuse application original APP is missing or outside vault")
    png_entry = frontmatter_scalar(original_application.read_text(encoding="utf-8-sig"), "png_entry")
    if not png_entry:
        raise AdapterStop("needs_review", "reuse application cannot borrow an empty original APP asset reference")

    slug = _reuse_trial_slug(trial_id)
    application_id = f"APP-{source_id}-REUSE-{trial_id}".upper()
    target = vault / "applications" / f"{date.today():%Y}" / f"{date.today():%m}" / f"APP-{source_id}-reuse-{slug}.md"
    application_entry = vault_relative(target)
    original_entry = vault_relative(original_application)
    asset_dir = vault / "assets" / source_id.casefold() / "derived-trials" / slug
    source_assets = [work_source, *[(root / path).resolve() for path in original_evidence_paths if path != original_work_path]]
    asset_by_origin: dict[str, str] = {}
    for index, source_asset in enumerate(source_assets, start=1):
        try:
            origin = source_asset.relative_to(root).as_posix()
        except ValueError as exc:
            raise AdapterStop("needs_review", "reuse trial asset escaped workspace") from exc
        destination = asset_dir / f"{index:02d}-{source_asset.name}"
        asset_by_origin[origin] = vault_relative(destination)
    work_path = asset_by_origin[original_work_path]
    evidence_paths = [asset_by_origin[path] for path in original_evidence_paths]
    adoption_status = str(trial.get("adoption_status") or "試行").strip()
    adoption_reason = str(trial.get("adoption_reason") or "").strip()
    next_action = str(trial.get("next_action") or "").strip()
    if not adoption_reason or not next_action:
        raise AdapterStop("needs_review", "reuse trial must state adoption_reason and next_action")
    human_items = [str(item).strip() for item in trial.get("human_verification_items", []) if str(item).strip()] if isinstance(trial.get("human_verification_items"), list) else []
    if not human_items:
        human_items = ["在目標情境檢視後，才調整這筆衍生應用紀錄。"]
    assessment_dimensions = [str(item["dimension"]) for item in assessments]
    assessment_statuses = [f"{item['dimension']}:{item['status']}" for item in assessments]
    assessment_summaries = [str(item["summary"]) for item in assessments]
    assessment_lines = "".join(
        f"- {item['dimension']}: {item['status']}；{item['summary']}（" + ", ".join(
            _reuse_markdown_link(target, vault / asset_by_origin[str(path)], "證據") for path in item["evidence_paths"]
        ) + ")\n"
        for item in assessments
    )
    frontmatter = {
        "type": "application",
        "application_id": application_id,
        "title": f"{title} — 研究轉用試驗",
        "source_id": source_id,
        "source_identity": source_identity,
        "source_note": source_note.stem,
        "source_entry": vault_relative(source_note),
        "application_entry": application_entry,
        "original_application_note": original_entry,
        "related_applications": [original_entry],
        "knowledge_points_entry": f"knowledge-points/by-source/{source_id}",
        "png_entry": png_entry,
        "ingest_status": "完整",
        "next_action": next_action,
        "issue_reason": "；".join(limitations),
        "adoption_status": adoption_status,
        "adoption_reason": adoption_reason,
        "ai_adoption_status": adoption_status,
        "ai_adoption_reason": adoption_reason,
        "ai_review_confidence": "中",
        "manual_correction_status": "無修正",
        "manual_correction_action": "如後續證據改變結論，才更新本衍生紀錄。",
        "taste_network_status": "不入網",
        "taste_network_reason": "這是單一來源的技術轉用紀錄，不作個人審美偏好證據。",
        "personalization_status": "linked",
        "personalization_links": [original_entry],
        "taste_link_status": "not_applicable",
        "taste_links": [],
        "human_verification_items": human_items,
        "life_scenes": ["工作", "學習"],
        "compounding_insight_status": "needs_context",
        "compounding_insight_types": [],
        "compounding_insight_links": [],
        "compounding_next_action": next_action,
        "application_status": str(trial.get("application_status") or adoption_status),
        "derivation_kind": "research_reuse_trial",
        "research_topic_id": topic_id,
        "trial_id": trial_id,
        "reuse_method": reuse_method,
        "work_path": work_path,
        "evidence_paths": evidence_paths,
        "origin_work_path": original_work_path,
        "origin_evidence_paths": original_evidence_paths,
        "assessment_dimensions": assessment_dimensions,
        "assessment_statuses": assessment_statuses,
        "assessment_summaries": assessment_summaries,
        "lesson": lesson,
        "limitations": limitations,
        "created": str(date.today()),
        "updated": str(date.today()),
    }
    text = (
        render_frontmatter(frontmatter)
        + f"# {title} — 研究轉用試驗\n\n"
        + "> 此紀錄是既有來源的衍生應用，不新增來源身份，也不改動原 APP、KP、資產或 completion receipt。\n\n"
        + f"- 來源筆記：{_reuse_markdown_link(target, source_note, '來源筆記')}\n"
        + f"- 原應用筆記：{_reuse_markdown_link(target, original_application, '原應用筆記')}\n"
        + f"- 試驗作品：{_reuse_markdown_link(target, vault / work_path, '試驗作品')}\n\n"
        + f"## AI 採納判斷\n\n- 狀態:{adoption_status}\n- 一句原因:{adoption_reason}\n\n"
        + "## 人工修正\n\n- 狀態:無修正\n- 可做動作:如後續證據改變結論，才更新本衍生紀錄。\n\n"
        + "## 品味網判斷\n\n- 狀態:不入網\n- 原因:單一來源的技術轉用紀錄，不作個人審美偏好證據。\n\n"
        + f"## 個人化扣連狀態\n\n- 狀態:linked\n- 扣連:{original_entry}\n\n"
        + "## 品味扣連狀態\n\n- 狀態:not_applicable\n\n"
        + f"## 跨來源洞見判斷\n\n- 狀態:needs_context\n- 下一步:{next_action}\n"
        + "- 補底:需要更多脈絡；尚無第二個已確認來源。\n- 加深:需要更多脈絡；尚無可比較的成熟作品。\n- 連接:需要更多脈絡；只連回同一來源。\n- 修正:需要更多脈絡；沒有第二項結論可反證。\n- 行動:需要更多脈絡；待真人情境確認。\n\n"
        + "## 仍需人工驗證事項\n\n" + "".join(f"- {item}\n" for item in human_items) + "\n"
        + "## 個人應用摘要\n\n### 方法\n\n"
        + f"{reuse_method}\n\n### 證據\n\n" + "".join(f"- {_reuse_markdown_link(target, vault / item, item)}\n" for item in evidence_paths)
        + "\n### 評估\n\n" + assessment_lines + "\n"
        + f"### 教訓\n\n{lesson}\n\n### 限制\n\n" + "".join(f"- {item}\n" for item in limitations)
    )
    with vault_write_lock(vault):
        for index, source_asset in enumerate(source_assets, start=1):
            destination = asset_dir / f"{index:02d}-{source_asset.name}"
            if destination.exists():
                if destination.read_bytes() != source_asset.read_bytes():
                    raise AdapterStop("blocked", "reuse trial asset target already exists with different content; do not overwrite it")
                continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            staging_asset = destination.with_name(destination.name + ".staging")
            if staging_asset.exists():
                raise AdapterStop("blocked", "reuse trial asset staging path already exists; inspect interrupted write")
            staging_asset.write_bytes(source_asset.read_bytes())
            if staging_asset.read_bytes() != source_asset.read_bytes():
                raise AdapterStop("blocked", "reuse trial asset staging readback failed")
            os.replace(staging_asset, destination)
            if destination.read_bytes() != source_asset.read_bytes():
                raise AdapterStop("blocked", "reuse trial asset readback failed")
        if target.exists():
            if target.read_text(encoding="utf-8-sig") != text:
                raise AdapterStop("blocked", "reuse application target already exists with different content; do not overwrite it")
            status = "replayed"
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            staging = target.with_suffix(".md.staging")
            staging.write_text(text, encoding="utf-8")
            if staging.read_text(encoding="utf-8") != text:
                raise AdapterStop("blocked", "reuse application staging readback failed")
            os.replace(staging, target)
            if target.read_text(encoding="utf-8") != text:
                raise AdapterStop("blocked", "reuse application readback failed")
            status = "written"
    return {"status": status, "application_note_path": application_entry, "original_application_note_path": original_entry}


def yaml_scalar(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def vault_relative(path: Path | None) -> str:
    if path is None:
        return ""
    parts = path.parts
    try:
        configured_parts = vault_relative_path().parts
    except WorkspacePathError:
        configured_parts = ()
    if configured_parts:
        width = len(configured_parts)
        for index in range(0, len(parts) - width + 1):
            if tuple(parts[index:index + width]) == configured_parts:
                return Path(*parts[index + width :]).as_posix()
    if "vault" in parts:
        index = parts.index("vault")
        return Path(*parts[index + 1 :]).as_posix()
    return path.as_posix()


def concept_title_from_knowledge_point(text: str) -> str:
    cleaned = re.sub(r"\s+", " ", text).strip(" 。.；;")
    for phrase in CONCEPT_REVIEW_PHRASES:
        cleaned = cleaned.replace(phrase, "")
    cleaned = cleaned.strip(" ，,。.;；")
    if not cleaned:
        return "未命名概念"
    if cleaned in CONCEPT_TITLE_EXACT_OVERRIDES:
        return CONCEPT_TITLE_EXACT_OVERRIDES[cleaned]
    contrast_title = concept_contrast_title(cleaned)
    if contrast_title:
        return contrast_title
    clause_title = concept_clause_title(cleaned)
    if clause_title:
        return clause_title
    for marker in ("形成對比", "是一種", "是把", "是由", "是讓", "是", "會", "可由", "可作", "須", "應"):
        index = cleaned.find(marker)
        if 1 < index <= 24:
            candidate = cleaned[:index].strip(" ，,、")
            if concept_title_is_usable(candidate, marker):
                return candidate
    return concept_trimmed_title(cleaned)


def concept_clause_title(text: str) -> str:
    for marker in ("，", ",", "；", ";", "。", "."):
        index = text.find(marker)
        if 4 <= index <= 48:
            candidate = text[:index].strip(" ，,、")
            if concept_title_is_usable(candidate, marker):
                return candidate
    return ""


def concept_trimmed_title(text: str) -> str:
    candidate = text[:48].strip(" ，,、。.;；")
    while title_has_bad_ending(candidate) and len(candidate) > 8:
        parts = candidate.rsplit(" ", 1)
        if len(parts) == 2 and len(parts[1]) <= 12:
            candidate = parts[0].strip(" ，,、。.;；")
            continue
        candidate = candidate[:-1].strip(" ，,、。.;；")
    return candidate or "未命名概念"


def concept_title_is_usable(candidate: str, marker: str) -> bool:
    if len(candidate) <= 3:
        return False
    if title_has_bad_ending(candidate):
        return False
    if marker == "應" and candidate.endswith("不"):
        return False
    if marker == "是" and (candidate.endswith("不") or candidate.endswith("只")):
        return False
    return True


def title_has_bad_ending(title: str) -> bool:
    lowered = title.casefold()
    return title.endswith(CONCEPT_TITLE_BAD_ENDINGS) or lowered.endswith(CONCEPT_TITLE_BAD_ENGLISH_ENDINGS)


def concept_contrast_title(text: str) -> str:
    direct_match = re.match(r"(.{1,24}心態)與(.{1,24}心態)形成對比", text)
    if direct_match:
        return f"{direct_match.group(1)}與{direct_match.group(2)}"
    clauses = [clause.strip() for clause in re.split(r"[，,；;]", text) if clause.strip()]
    labels: list[str] = []
    for clause in clauses:
        for match in re.finditer(r"([\u4e00-\u9fffA-Za-z0-9 ]{1,12}心態)", clause):
            label = match.group(1).strip(" ，,、")
            if label and label not in labels:
                labels.append(label)
    if len(labels) >= 2 and "重視" in text:
        return f"{labels[0]}與{labels[1]}"
    return ""


def safe_note_stem(value: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', " ", value)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")
    return (cleaned or "未命名概念")[:80].strip()


def concept_note_path(vault: Path, knowledge_point: str) -> Path:
    title = concept_title_from_knowledge_point(knowledge_point)
    target = vault / "concepts" / f"{safe_note_stem(title)}.md"
    if not target.resolve().is_relative_to(vault.resolve()):
        raise AdapterStop("blocked", "resolved concept target escapes the selected Vault")
    return target


def application_link_for_source(vault: Path, source_id: str) -> str:
    candidates = sorted((vault / "applications").glob(f"**/APP-{source_id}_*.md"))
    return candidates[0].stem if candidates else ""


def plain_record_id(value: str) -> str:
    value = value.strip()
    match = re.fullmatch(r"\[\[([^\]|]+)(?:\|[^\]]+)?\]\]", value)
    return match.group(1) if match else value


def concept_title_quality_issue(title: str) -> str:
    if any(phrase in title for phrase in CONCEPT_REVIEW_PHRASES):
        return "concept_title_has_review_phrase"
    if "重視" in title and len(re.findall(r"心態", title)) >= 2:
        return "concept_title_has_unreduced_contrast_sentence"
    if len(title) <= 3:
        return "concept_title_too_short"
    if len(title) > 48:
        return "concept_title_too_long"
    if title_has_bad_ending(title):
        return "concept_title_bad_ending"
    if ";" in title or "；" in title:
        return "concept_title_has_semicolon"
    if re.search(r"\b[\w.-]+\.md\b", title):
        return "concept_title_has_file_name"
    return ""


def is_generated_concept_note(path: Path) -> bool:
    fields = frontmatter_fields(path)
    if fields.get("type") != "concept":
        return False
    if fields.get("concept_status") not in {"graph_node", "superseded"}:
        return False
    if fields.get("graph_scope") != "concept_links_only":
        return False
    text = path.read_text(encoding="utf-8-sig")
    return "knowledge_point_records:" in text


def render_concept_note(entry: dict[str, object], updated: str | None = None) -> str:
    concept = str(entry["concept"])
    source_records = sorted(entry["source_records"])
    application_records = sorted(entry["application_records"])
    knowledge_point_records = sorted(entry["knowledge_point_records"])
    related_concepts = sorted(entry["related_concepts"])
    definitions = sorted(entry["definitions"])
    frontmatter = {
        "type": "concept",
        "concept": concept,
        "idea_type": "concept",
        "graph_group": "concept",
        "concept_status": entry.get("concept_status", "graph_node"),
        "graph_layer": "概念層",
        "graph_role": "概念",
        "graph_question": "這個概念在其他來源或情境中是否仍然成立？",
        "graph_scope": "concept_links_only",
        "source_records": source_records,
        "application_records": application_records,
        "knowledge_point_records": knowledge_point_records,
        "related_concepts": related_concepts,
        "updated": updated or str(date.today()),
    }
    body = render_frontmatter(frontmatter)
    body += f"# {concept}\n\n"
    body += "> 這是給 Obsidian 關聯圖使用的語義節點；圖譜只連中文概念，來源、應用與知識點保留為可檢視記錄。\n\n"
    body += "## 圖譜用途\n\n"
    body += "- 角色:概念\n"
    body += "- 可追問:這個概念在其他來源或情境中是否仍然成立？\n\n"
    if entry.get("concept_status") == "superseded":
        body += "> 此舊生成節點已由有來源證據的內容修訂撤回；保留原路徑，不再作為有效概念取材。\n\n"
    body += "## 定義來源\n\n"
    for definition in definitions:
        body += f"- {definition}\n"
    body += "\n## 知識點記錄\n\n"
    for record in knowledge_point_records:
        body += f"- `{record}`\n"
    body += "\n## 來源記錄\n\n"
    for record in source_records:
        body += f"- `{record}`\n"
    if application_records:
        body += "\n## 應用記錄\n\n"
        for record in application_records:
            body += f"- `{record}`\n"
    if related_concepts:
        body += "\n## 相關概念\n\n"
        for link in related_concepts:
            body += f"- {link}\n"
    body += str(entry.get("registry_sections") or "")
    return body


def concept_graph_role(concept: str, definitions: list[str]) -> str:
    text = " ".join([concept, *definitions]).casefold()
    if any(marker in text for marker in ("品味", "生活", "策展", "選擇", "判斷")):
        return "品味線索"
    if any(marker in text for marker in ("workflow", "流程", "步驟", "模式", "方法")):
        return "方法"
    if any(marker in text for marker in ("mcp", "properties", "bases", "json", "yaml", "工具", "系統")):
        return "工具/結構"
    if any(marker in text for marker in ("價值", "information gain", "稀缺", "新增")):
        return "判斷準則"
    return "概念"


def concept_graph_group(concept: str, definitions: list[str]) -> str:
    """Choose a display colour without upgrading a concept into a formal idea."""
    text = " ".join([concept, *definitions]).casefold()
    if any(marker in text for marker in (" vs ", "vs", "取捨", "矛盾", "衝突", "兩難", "界線", "限制")):
        return "tension"
    if any(marker in text for marker in ("品味", "偏好", "審美", "吸引", "抗拒", "信任", "懷疑")):
        return "taste_signal"
    if concept_graph_role(concept, definitions) in {"方法", "工具/結構"} or any(
        marker in text for marker in ("如何", "怎樣", "應用", "實作", "工作", "學習", "創作", "使用", "場景")
    ):
        return "application_scene"
    return "hub"


def concept_graph_question(concept: str, definitions: list[str]) -> str:
    role = concept_graph_role(concept, definitions)
    if role == "品味線索":
        return "這是否反覆出現在 使用者 想要的生活、工作方式或審美選擇中？"
    if role == "方法":
        return "這能否變成可重複使用的工作流程或檢查清單？"
    if role == "工具/結構":
        return "這是否只是工具細節，還是會改變資料、流程或決策方式？"
    if role == "判斷準則":
        return "這是否能幫 使用者 更快判斷內容值得採納還是應該略過？"
    return "它和哪些概念同時出現，是否形成新的主題或問題？"


def knowledge_point_source_id(fields: dict[str, object]) -> str:
    """Read the source identity from a generated knowledge-point record."""
    explicit = str(fields.get("source_id") or "").strip()
    if explicit:
        return explicit
    return str(fields.get("kp_uid") or "").split(":", 1)[0].strip()


def concept_is_active(fields: dict) -> bool:
    """Legacy/manual notes stay readable; explicit withdrawals are never ideas."""
    return fields.get("concept_status") not in {"superseded", "withdrawn"}


def reusable_concept_decision(fields: dict) -> str:
    """One shared owner for compiler projection and later missing-note rebuild."""
    if fields.get("reusable_concept_status") != "reusable":
        return ""
    name = str(fields.get("reusable_concept_name") or "").strip()
    return name if name and fields.get("reusable_concept_reason") else ""


def reusable_concept_note_path(vault: Path, concept: str) -> Path:
    """Sole owner of the analysed-concept note path (reusable schema)."""
    path = vault / "concepts" / f"{safe_note_stem(concept)}.md"
    if not path.resolve().is_relative_to(vault.resolve()):
        raise AdapterStop("blocked", "resolved concept target escapes the selected Vault")
    return path


def projected_concept_note_path(vault: Path, fields: dict, fallback_text: str = "") -> Path | None:
    """Where this written knowledge-point note expects its concept note, or None.

    New schema (carries `reusable_concept_status`): only an analysed reusable
    concept has a note, named by the analysed name, never guessed from the title.
    Legacy schema (field absent): keep the historical title-derived projection.
    """
    if fields.get("reusable_concept_status"):
        concept = reusable_concept_decision(fields)
        return reusable_concept_note_path(vault, concept) if concept else None
    text = str(fields.get("knowledge_point") or fields.get("definition") or fallback_text)
    return concept_note_path(vault, text) if text else None


def planned_concept_note_path(vault: Path, kp: "KnowledgePoint", note: Path) -> Path | None:
    """Concept note for one planned KP: the written note's frontmatter, else what the writer will write."""
    fields = frontmatter_fields(note) if note.is_file() else {
        "reusable_concept_status": kp.reusable_concept_status,
        "reusable_concept_name": kp.reusable_concept_name,
        "reusable_concept_reason": kp.reusable_concept_reason,
        "knowledge_point": kp.text,
    }
    return projected_concept_note_path(vault, fields, kp.text)


# Registry-derived concept-page blocks (semantic-index spec section 2: the sole definition).
# (relation_type, section heading, this concept is the newer side, this concept is the older side, both sides in one concept)
CROSS_SOURCE_SECTIONS = (
    ("supports", "支持", "此概念支持 {c}", "此概念被 {c} 支持", "同一概念內，新來源支持舊來源"),
    ("contradicts", "反例", "此概念反駁 {c}", "此概念被 {c} 反駁", "同一概念內，新來源反駁舊來源"),
    ("limits", "限制", "此概念限制 {c}", "此概念受 {c} 限制", "同一概念內，新來源限制舊來源"),
    ("extends", "延伸", "此概念延伸 {c}", "此概念被 {c} 延伸", "同一概念內，新來源延伸舊來源"),
    ("tradeoff", "形成取捨", "此概念與 {c} 形成取捨", "此概念與 {c} 形成取捨", "同一概念內，新舊來源形成取捨"),
    ("applies_to", "可應用於", "此概念可應用於 {c}", "{c} 可應用於此概念", "同一概念內，新來源可應用於舊來源"),
)
COUNTERPART_CLAIM_CHARS = 40
# Every heading the registry-derived tail of a concept note can carry (render_registry_sections), in no particular order.
REGISTRY_BLOCK_HEADINGS = ("目前理解（AI 解讀）", *(heading for _, heading, *_ in CROSS_SOURCE_SECTIONS), "來源數")


def counterpart_claim_label(claim: str, limit: int = COUNTERPART_CLAIM_CHARS) -> str:
    """The claim as a counterpart label: whole when it fits, else cut at a word boundary with "…" (never inside an English word)."""
    if len(claim) <= limit:
        return claim
    cut = claim[:limit]
    if claim[limit - 1].isascii() and claim[limit - 1].isalnum() and claim[limit].isascii() and claim[limit].isalnum():
        trimmed = re.sub(r"[A-Za-z0-9]+$", "", cut)  # the cut falls inside an English word or number: leave that word out
        cut = trimmed if trimmed.strip() else cut  # a single word longer than the limit keeps the hard cut
    return cut.rstrip(" ,，、;；:：-") + "…"


def plain_projection_text(value: object) -> str:
    """One line of plain text for a concept page: no line breaks, no wikilink brackets."""
    return re.sub(r"\s+", " ", str(value or "").replace("[[", "").replace("]]", "")).strip()


def concept_projection_facts_from_rows(kp_rows: dict[str, dict[str, str]],
                                       relation_rows: dict[str, dict[str, str]]) -> dict[str, object] | None:
    """The registry rows concept pages project, or None when there is nothing to show.

    Only linked cross-source relations whose two knowledge points are both linked
    count; ``cross_all`` keeps every cross-source row, whatever its status, so the
    concepts of a withdrawn relation are reprojected too.
    """
    relation_rows = list(relation_rows.values())
    cross_all = [row for row in relation_rows if row.get("scope") == "cross_source"]
    relations = {relation for relation, *_ in CROSS_SOURCE_SECTIONS}

    def linked(uid: str) -> bool:
        return uid in kp_rows and kp_rows[uid].get("status", "linked") == "linked"

    links = sorted(
        (row for row in cross_all
         if row.get("status", "linked") == "linked" and row.get("relation") in relations
         and linked(str(row.get("source_kp_uid"))) and linked(str(row.get("target_kp_uid")))),
        key=lambda row: str(row.get("relation_id")))
    deltas = {uid: row for uid, row in kp_rows.items()
              if linked(uid) and str(row.get("concept_name") or "").strip() and str(row.get("understanding_delta") or "").strip()}
    if not links and not deltas and not cross_all:
        return None
    return {"kp": kp_rows, "links": links, "deltas": deltas, "cross_all": cross_all}


def concept_projection_facts(vault: Path) -> dict[str, object] | None:
    """Read the live registries for the ordinary concept-page projection."""
    return concept_projection_facts_from_rows(read_knowledge_point_registry(vault), read_relation_registry(vault))


def cross_source_affected_uids(facts: dict[str, object] | None, source_ids: set[str]) -> set[str]:
    """Knowledge points at both ends of every cross-source relation that touches the named sources."""
    uids: set[str] = set()
    for row in (facts or {}).get("cross_all", []):
        if row.get("source_id") in source_ids or row.get("target_source_id") in source_ids:
            uids.update((str(row.get("source_kp_uid")), str(row.get("target_kp_uid"))))
    return uids


def cross_source_affected_paths(facts: dict[str, object] | None, source_ids: set[str],
                                kp_uid_to_concept: dict[str, Path]) -> set[Path]:
    """Concept notes of both ends of every cross-source relation that touches the named sources."""
    return {kp_uid_to_concept[uid] for uid in cross_source_affected_uids(facts, source_ids) if uid in kp_uid_to_concept}


def concept_counterpart_text(uid: str, kp_rows: dict[str, dict[str, str]], uid_concept: dict[str, tuple[str, str]]) -> str:
    """The other end of a relation: a concept wikilink, or the knowledge point's own words when it has no concept page."""
    if uid in uid_concept:
        stem, name = uid_concept[uid]
        return f"[[{stem}]]" if stem == name else f"[[{stem}|{name}]]"
    claim = plain_projection_text(kp_rows.get(uid, {}).get("knowledge_point"))
    return f"「{counterpart_claim_label(claim)}」"


def render_registry_sections(facts: dict[str, object] | None, entry: dict[str, object],
                             uid_concept: dict[str, tuple[str, str]]) -> str:
    """Blocks for one concept note, from the registry only; "" when the concept has no data (its bytes stay as before)."""
    own = set(entry.get("kp_uids") or ())
    if not facts or not own:
        return ""
    kp_rows = facts["kp"]
    source_of = lambda uid: str(kp_rows.get(uid, {}).get("source_id") or "")
    deltas = sorted((str(facts["deltas"][uid].get("updated") or ""), source_of(uid), uid) for uid in own if uid in facts["deltas"])
    lines: dict[str, set[str]] = {relation: set() for relation, *_ in CROSS_SOURCE_SECTIONS}
    wording = {relation: rest for relation, _, *rest in CROSS_SOURCE_SECTIONS}
    for link in facts["links"]:
        source_uid, target_uid = str(link["source_kp_uid"]), str(link["target_kp_uid"])
        source_in, target_in = source_uid in own, target_uid in own
        if not (source_in or target_in):
            continue
        newer_source = source_of(source_uid) or str(link.get("source_id") or "")
        older_source = str(link.get("target_source_id") or "") or source_of(target_uid)
        insight = plain_projection_text(link.get("insight"))
        newer_side, older_side, within = wording[str(link["relation"])]
        if source_in and target_in:
            line = f"- {within}：{insight}（新來源 `{newer_source}`；舊來源 `{older_source}`）"
        elif source_in:
            counterpart = concept_counterpart_text(target_uid, kp_rows, uid_concept)
            line = f"- {newer_side.format(c=counterpart)}：{insight}（此概念方來源 `{newer_source}`；對方來源 `{older_source}`）"
        else:
            counterpart = concept_counterpart_text(source_uid, kp_rows, uid_concept)
            line = f"- {older_side.format(c=counterpart)}：{insight}（此概念方來源 `{older_source}`；對方來源 `{newer_source}`）"
        lines[str(link["relation"])].add(line)
    if not deltas and not any(lines.values()):
        return ""
    text = ""
    if deltas:
        def delta_line(uid: str, lead: str = "") -> str:
            row = facts["deltas"][uid]
            job = f"；工作 `{row['job_id']}`" if row.get("job_id") else ""
            return f"- {lead}{plain_projection_text(row['understanding_delta'])}（來源 `{source_of(uid)}`{job}）\n"
        text += "\n## 目前理解（AI 解讀）\n\n> 以下是 AI 依各來源的分析寫成的解讀，不是原文引述；每條附來源編號與工作編號。\n\n"
        text += delta_line(deltas[-1][2], "最新：")
        if len(deltas) > 1:
            text += "\n歷次修正（舊至新）：\n\n" + "".join(delta_line(uid) for _, _, uid in deltas[:-1])
    for relation, heading, *_ in CROSS_SOURCE_SECTIONS:
        if lines[relation]:
            text += f"\n## {heading}\n\n" + "".join(line + "\n" for line in sorted(lines[relation]))
    return text + f"\n## 來源數\n\n- 連到此概念的不同來源：{len(entry['source_records'])}\n"


def concept_page_index(vault: Path, entries: dict[Path, dict], kp_uid_to_concept: dict[str, Path],
                       *, revised_uids: set[str] | None = None
                       ) -> tuple[dict[str, tuple[str, str]], dict[Path, dict]]:
    """(kp_uid -> (note stem, concept name), generated pages the new-format entries do not cover).

    The kp_uid-to-page map is the idea retrieval's own (``concept_notes_with_kp_uids``: frontmatter
    ``knowledge_point_records`` through ``prior_kps_for_note``), so a page the candidate search offers is a page the
    projection can name.  The analysed concept of a new-format point wins; every other point takes the first page by
    path, legacy and handwritten pages included (a wikilink target only).  The second value holds the active generated
    pages outside ``entries`` (older format: their own frontmatter and body are kept) as minimal entries.
    """
    try:
        from tools.obsidian_idea_retrieval import concept_name_of, concept_notes_with_kp_uids
    except ImportError:
        from obsidian_idea_retrieval import concept_name_of, concept_notes_with_kp_uids  # type: ignore
    uid_concept: dict[str, tuple[str, str]] = {}
    legacy: dict[Path, dict] = {}
    for note, uids in concept_notes_with_kp_uids(vault):
        if revised_uids is not None:
            # Live membership is not evidence of the candidate's membership.
            # Remove old assignments before adding the surviving/new ones below.
            uids = set(uids) - revised_uids
            if note.path in entries:
                if not concept_is_active(entries[note.path]):
                    continue
                uids &= set(entries[note.path].get("kp_uids") or ())
        name = str(entries[note.path]["concept"]) if note.path in entries else concept_name_of(note)
        for uid in uids:
            uid_concept.setdefault(uid, (note.path.stem, name))
        if note.path not in entries and is_generated_concept_note(note.path):
            legacy[note.path] = {"concept": name, "source_records": set(note.source_records), "kp_uids": set(uids)}
    uid_concept.update({
        uid: (path.stem, str(entries[path]["concept"]))
        for uid, path in kp_uid_to_concept.items()
        if path in entries and (revised_uids is None or concept_is_active(entries[path]))
    })
    return uid_concept, legacy


def attach_registry_sections(vault: Path, entries: dict[Path, dict], kp_uid_to_concept: dict[str, Path],
                             facts: dict[str, object] | None = None,
                             *, revised_uids: set[str] | None = None) -> None:
    facts = concept_projection_facts(vault) if facts is None else facts
    if facts is None:
        return
    uid_concept, _ = concept_page_index(vault, entries, kp_uid_to_concept, revised_uids=revised_uids)
    for entry in entries.values():
        entry["registry_sections"] = render_registry_sections(facts, entry, uid_concept)


def build_reusable_concept_entries(vault: Path, candidate_fields: dict[Path, dict] | None = None,
                                   facts: dict[str, object] | None = None):
    entries, kp_uid_to_concept = {}, {}
    for kp_file in sorted((vault / "knowledge-points" / "by-source").glob("*/*.md")):
        fields = (candidate_fields or {}).get(kp_file, frontmatter_fields(kp_file))
        concept = reusable_concept_decision(fields)
        if fields.get("type") != "knowledge_point" or not concept:
            continue
        path = reusable_concept_note_path(vault, concept)
        entry = entries.setdefault(path, {"concept": concept, "source_records": set(),
            "application_records": set(), "knowledge_point_records": set(),
            "related_concepts": set(), "definitions": set(), "source_ids": set(), "kp_uids": set()})
        if entry["concept"] != concept:
            raise AdapterStop("blocked", "different analysed concepts resolve to the same note name")
        entry["knowledge_point_records"].add(kp_file.stem)
        if fields.get("kp_uid"): entry["kp_uids"].add(str(fields["kp_uid"]))
        entry["definitions"].add(str(fields.get("knowledge_point") or fields.get("definition") or ""))
        source_note = str(fields.get("source_note") or "")
        source_id = knowledge_point_source_id(fields)
        if source_note: entry["source_records"].add(plain_record_id(source_note))
        app_link = application_link_for_source(vault, source_id)
        if app_link: entry["application_records"].add(app_link)
        if source_id: entry["source_ids"].add(source_id)
        if fields.get("kp_uid"): kp_uid_to_concept[str(fields["kp_uid"])] = path
    for path, entry in entries.items():
        if not path.exists() or not is_generated_concept_note(path): continue
        existing = frontmatter_fields(path)
        entry["related_concepts"].update(fields_list(existing, "related_concepts"))
        for record in set(fields_list(existing, "knowledge_point_records")) - entry["knowledge_point_records"]:
            matches = list((vault / "knowledge-points" / "by-source").glob(f"*/{record}.md"))
            if not matches:
                # Incomplete legacy provenance cannot authorize subtraction.
                for key in ("source_records", "application_records", "knowledge_point_records"):
                    entry[key].update(fields_list(existing, key))
                entry["definitions"].update(existing_definition_lines(path))
                continue
            for point in matches:
                legacy = (candidate_fields or {}).get(point, frontmatter_fields(point))
                if legacy.get("reusable_concept_status"):
                    continue
                entry["knowledge_point_records"].add(record)
                if legacy.get("kp_uid"): entry["kp_uids"].add(str(legacy["kp_uid"]))
                entry["definitions"].add(str(legacy.get("knowledge_point") or legacy.get("definition") or ""))
                source_note = str(legacy.get("source_note") or "")
                if source_note: entry["source_records"].add(plain_record_id(source_note))
                app = application_link_for_source(vault, knowledge_point_source_id(legacy))
                if app: entry["application_records"].add(app)
    revised_uids = ({str(fields["kp_uid"]) for fields in candidate_fields.values() if fields.get("kp_uid")}
                    if candidate_fields is not None else None)
    attach_registry_sections(vault, entries, kp_uid_to_concept, facts, revised_uids=revised_uids)
    return entries, kp_uid_to_concept


def registry_block_start(text: str) -> int | None:
    """Offset of the registry-derived tail of a concept note, or None when it has none.

    The tail is the run of registry headings that ends the note and closes with ``## 來源數``, each section written in
    the registry-line shape.  A legacy page's own sections of the same name (plain wikilink lists) never qualify."""
    heads = list(re.finditer(r"^## (.+?)[ \t]*\r?$", text, re.M))
    if not heads or heads[-1].group(1) != "來源數":
        return None
    start = None
    for index in range(len(heads) - 1, -1, -1):
        name = heads[index].group(1)
        end = heads[index + 1].start() if index + 1 < len(heads) else len(text)
        body = text[heads[index].end():end]
        lines = [line.strip() for line in body.splitlines() if line.strip()]
        if name == "來源數":
            qualifies = len(lines) == 1 and re.fullmatch(r"- 連到此概念的不同來源：\d+", lines[0]) is not None
        elif name == "目前理解（AI 解讀）":
            qualifies = any("不是原文引述" in line for line in lines)
        else:
            qualifies = name in REGISTRY_BLOCK_HEADINGS and bool(lines) and all(
                line.startswith("- ") and re.search(r"（(?:此概念方來源|新來源) `", line) for line in lines)
        if not qualifies:
            break
        start = heads[index].start()
    return start


def splice_registry_sections(text: str, sections: str) -> str:
    """A legacy-format concept note with its registry tail replaced by ``sections``; every other byte stays.

    ``sections`` is ``render_registry_sections`` output ("" removes the tail).  The tail takes the note's own line ending,
    and a note that already carries exactly this tail comes back unchanged."""
    eol = "\r\n" if "\r\n" in text else "\n"
    start = registry_block_start(text)
    head = text if start is None else text[:start]
    if start is not None and head.endswith(eol):
        head = head[:-len(eol)]  # the blank line the tail's own leading newline made
    return head + sections.replace("\n", eol)


def scoped_concept_revision(vault: Path, source_id: str, old_ids: set[str], concepts: dict[Path, bytes],
                            facts: dict[str, object] | None = None,
                            candidate_kp_concepts: dict[str, tuple[Path, str]] | None = None):
    """Same-path withdrawal; no deletion, relocation, handwritten or global prune."""
    facts = concept_projection_facts(vault) if facts is None else facts
    original_points = {p.stem: frontmatter_fields(p) for p in
        (vault / "knowledge-points" / "by-source" / source_id).glob("*.md")}
    old_definitions = {str(f.get("knowledge_point") or f.get("definition") or "") for f in original_points.values()}
    old_sources = {plain_record_id(str(f.get("source_note") or "")) for f in original_points.values()}
    old_apps = {application_link_for_source(vault, source_id)} - {""}
    affected = {p for p in (vault / "concepts").glob("*.md") if is_generated_concept_note(p)
                and set(fields_list(frontmatter_fields(p), "knowledge_point_records")).intersection(old_ids)}
    staged_entries: list[tuple[Path, dict, bool]] = []
    for path in sorted(affected | set(concepts)):
        if path.exists() and not is_generated_concept_note(path): continue
        existing = frontmatter_fields(path) if path.exists() else {}
        incoming = frontmatter_fields_from_text(concepts[path].decode("utf-8")) if path in concepts else {}
        # Retain all other-source contributions, even if their old KP did not
        # yet carry the new analysis contract. Exact source records/IDs bound
        # the subtraction; title resemblance is never ownership evidence.
        retained_ids = set(fields_list(existing, "knowledge_point_records")) - old_ids
        retained_sources = set(fields_list(existing, "source_records")) - old_sources
        retained_apps = set(fields_list(existing, "application_records")) - old_apps
        retained_defs = set()
        for record in retained_ids:
            for kp in (vault / "knowledge-points" / "by-source").glob(f"*/{record}.md"):
                f = frontmatter_fields(kp)
                retained_defs.add(str(f.get("knowledge_point") or f.get("definition") or ""))
        if retained_sources and not retained_defs:
            # Old shared metadata without readable KP proof is preserved.
            retained_defs = set(existing_definition_lines(path)) - old_definitions
        entry = {"concept": str(incoming.get("concept") or existing.get("concept") or path.stem),
                 "source_records": retained_sources | set(fields_list(incoming, "source_records")),
                 "application_records": retained_apps | set(fields_list(incoming, "application_records")),
                 "knowledge_point_records": retained_ids | set(fields_list(incoming, "knowledge_point_records")),
                 "definitions": retained_defs | (set(existing_definition_lines_from_text(concepts[path].decode("utf-8"))) if path in concepts else set()),
                 "related_concepts": set(fields_list(existing, "related_concepts"))}
        if not entry["knowledge_point_records"] and not entry["source_records"]:
            entry.update(concept_status="superseded", related_concepts=set())
        records = set(entry["knowledge_point_records"])
        entry["kp_uids"] = {
            uid for uid, row in (facts or {}).get("kp", {}).items()
            if str(row.get("kp_id") or "") in records or Path(str(row.get("kp_note_path") or "")).stem in records
        }
        staged_entries.append((path, entry, bool(retained_sources)))
    withdrawn_names = {path.stem for path, entry, _ in staged_entries
                       if entry.get("concept_status") == "superseded"}
    for _, entry, _ in staged_entries:
        if entry.get("concept_status") != "superseded":
            entry["related_concepts"] = {link for link in entry["related_concepts"]
                                         if plain_record_id(str(link)) not in withdrawn_names}
    entry_map = {path: entry for path, entry, _ in staged_entries}
    kp_uid_to_concept = {uid: path for path, entry, _ in staged_entries for uid in entry["kp_uids"]}
    revised_uids = {knowledge_point_uid(source_id, kp_id) for kp_id in old_ids}
    uid_concept, legacy = concept_page_index(vault, entry_map, kp_uid_to_concept, revised_uids=revised_uids)
    for uid, (path, concept) in (candidate_kp_concepts or {}).items():
        if path not in entry_map and path.is_file() and not is_generated_concept_note(path):
            uid_concept[uid] = (path.stem, concept)
    changes = []
    for path, entry, shared in staged_entries:
        entry["registry_sections"] = render_registry_sections(facts, entry, uid_concept)
        concepts[path] = render_concept_note(entry).encode("utf-8")
        changes.append({"path": path, "action": "withdraw_source_contribution" if shared else
                        ("superseded_same_path" if entry.get("concept_status") == "superseded" else "replace_source_concept"),
                        "shared": shared})
    # Older-format counterparts have no new-format entry and are outside the
    # source-member rebuild above. Only their registry tail belongs to this
    # revision; preserve their original encoding, line endings and body.
    touched_uids = cross_source_affected_uids(facts, {source_id})
    for path, entry in sorted(legacy.items()):
        if not entry["kp_uids"].intersection(touched_uids):
            continue
        original = path.read_bytes().decode("utf-8")
        spliced = splice_registry_sections(original, render_registry_sections(facts, entry, uid_concept))
        if spliced != original:
            concepts[path] = spliced.encode("utf-8")
    return changes


def existing_definition_lines_from_text(text: str) -> list[str]:
    match = re.search(r"## 定義來源\s*\n(.*?)(?:\n## |\Z)", text, re.S)
    return [line[2:] for line in match.group(1).splitlines() if line.startswith("- ")] if match else []


def existing_definition_lines(path: Path) -> list[str]:
    return existing_definition_lines_from_text(path.read_text(encoding="utf-8-sig"))


def write_concept_notes(vault: Path, *, source_ids: set[str] | None = None,
                        candidate_fields: dict[Path, dict] | None = None,
                        render_only: bool = False,
                        legacy_touched: list[Path] | None = None,
                        facts: dict[str, object] | None = None) -> list[Path] | dict[Path, bytes]:
    """Project concept nodes, optionally touching only named source contributions.

    The ordinary graph command can rebuild the whole derived concept layer.  A
    formal intake instead asks for the just-written source only, keeping its
    source-local write bounded while making its graph view useful immediately.

    Generated pages of the older format (outside the new-format entries) keep every byte but their registry tail,
    which is spliced on; their paths go to ``legacy_touched`` and the return value stays the new-format pages.
    Handwritten pages are never written, and ``render_only`` (a reviewed revision) leaves the older pages alone.
    """
    assert_management_idle_for_vault(vault)
    selected_source_ids = {source_id.strip() for source_id in (source_ids or set()) if source_id.strip()}
    concept_dir = vault / "concepts"
    if not render_only:
        concept_dir.mkdir(parents=True, exist_ok=True)
    facts = concept_projection_facts(vault) if facts is None else facts
    entries, kp_uid_to_concept = build_reusable_concept_entries(vault, candidate_fields, facts)
    uid_concept, legacy = ({}, {}) if render_only else concept_page_index(vault, entries, kp_uid_to_concept)  # read before any write

    affected_paths = {p for p,e in entries.items() if set(e['source_ids']).intersection(selected_source_ids)}
    legacy_paths = set(legacy)
    if selected_source_ids:
        # The other end of a cross-source relation is an older concept: it must show the new relation too.
        affected_paths |= cross_source_affected_paths(facts, selected_source_ids, kp_uid_to_concept) & set(entries)
        touched_uids = cross_source_affected_uids(facts, selected_source_ids) | {
            uid for uid, row in (facts or {}).get("kp", {}).items() if row.get("source_id") in selected_source_ids}
        legacy_paths = {path for path, entry in legacy.items() if entry["kp_uids"] & touched_uids}
    # Source-local KP relations are evidence records, not reviewed idea bridges.
    # Obsolete derived names remain intact for an explicitly scoped review;
    # neither source intake nor graph rebuild authorises deletion/relocation.

    today = str(date.today())
    touched: list[Path] = []
    rendered: dict[Path, bytes] = {}
    for path, entry in entries.items():
        entry_source_ids = set(entry["source_ids"])
        if selected_source_ids and path not in affected_paths:
            continue
        if path.exists() and not is_generated_concept_note(path):
            continue  # Handwritten ideas are not adapter-owned projections.
        if path.exists() and not concept_is_active(frontmatter_fields(path)):
            continue  # Explicit withdrawal requires a reviewed revision to undo.
        existing_text = path.read_text(encoding="utf-8-sig") if path.exists() else ""
        existing_updated = ""
        if path.exists():
            existing_updated = str(frontmatter_fields(path).get("updated") or "")
        stable_render = render_concept_note(entry, existing_updated or today)
        if render_only:
            rendered[path] = (stable_render if existing_text == stable_render else render_concept_note(entry, today)).encode("utf-8")
            continue
        if existing_text != stable_render:
            path.write_text(render_concept_note(entry, today), encoding="utf-8")
        touched.append(path)
    for path in sorted(legacy_paths):
        original = path.read_bytes().decode("utf-8")
        spliced = splice_registry_sections(original, render_registry_sections(facts, legacy[path], uid_concept))
        if spliced != original:
            path.write_bytes(spliced.encode("utf-8"))
        if legacy_touched is not None:
            legacy_touched.append(path)
    return rendered if render_only else sorted(touched)


INFOGRAPHIC_SOURCE_OVERLAY_FIELDS = frozenset({
    "asset_mode", "infographic_status", "png_generation_method",
    "png_semantic_validation_status", "png_semantic_validation_evidence",
    "png_reverse_review_status", "png_reverse_review_evidence",
    "infographic_importance_coverage", "infographic_count_decision",
    "coverage_unit_infographic_mapped", "infographic_page_count",
    "infographic_multi_volume_status", "source_assets", "infographics", "updated",
})
INFOGRAPHIC_APPLICATION_OVERLAY_FIELDS = frozenset({
    "ingest_status", "next_action", "issue_reason", "png_entry", "入庫",
    "下一步", "問題", "PNG", "updated",
})
INFOGRAPHIC_SOURCE_OVERLAY_SECTIONS = (
    "## 覆蓋審計", "## 資訊圖",
)


def _frontmatter_entry(key: str, value: str | list[str]) -> str:
    if isinstance(value, list):
        if not value:
            return f"{key}: []"
        return "\n".join([f"{key}:", *(f"  - {yaml_scalar(item)}" for item in value)])
    return f"{key}: {yaml_scalar(value)}"


def _replace_frontmatter_fields(base: str, rendered: str, fields: frozenset[str]) -> str:
    """Copy only named generated frontmatter fields without normalizing the rest."""
    if not (base.startswith("---\n") and rendered.startswith("---\n")):
        raise ValueError("infographic overlay requires YAML frontmatter")
    base_end = base.find("\n---", 4)
    rendered_end = rendered.find("\n---", 4)
    if base_end == -1 or rendered_end == -1:
        raise ValueError("infographic overlay has incomplete YAML frontmatter")
    base_fields = frontmatter_fields_from_text(base)
    rendered_fields = frontmatter_fields_from_text(rendered)
    result = base
    for key in fields:
        if key not in base_fields or key not in rendered_fields:
            raise ValueError("infographic overlay field is missing: " + key)
        # Scalar fields occupy one line; list fields include their indented rows.
        pattern = re.compile(rf"(?m)^{re.escape(key)}:.*(?:\n  - .*)*")
        matches = list(pattern.finditer(result, 4, result.find("\n---", 4)))
        if len(matches) != 1:
            raise ValueError("infographic overlay field is ambiguous: " + key)
        value = rendered_fields[key]
        result = result[:matches[0].start()] + _frontmatter_entry(key, value) + result[matches[0].end():]
    return result


def _markdown_section(text: str, heading: str) -> str:
    match = re.search(rf"(?m)^{re.escape(heading)}\n", text)
    if not match:
        raise ValueError("infographic overlay section is missing: " + heading)
    next_heading = re.search(r"(?m)^##\s+", text[match.end():])
    end = match.end() + next_heading.start() if next_heading else len(text)
    return text[match.start():end]


def _replace_markdown_sections(base: str, rendered: str, headings: tuple[str, ...]) -> str:
    result = base
    for heading in headings:
        before = _markdown_section(result, heading)
        after = _markdown_section(rendered, heading)
        if result.count(before) != 1:
            raise ValueError("infographic overlay section is ambiguous: " + heading)
        result = result.replace(before, after, 1)
    return result


def render_infographic_overlay(
    *,
    source_before: bytes,
    source_rendered: str,
    application_before: bytes,
    application_rendered: str,
) -> tuple[bytes, bytes]:
    """Build a narrowly-scoped visual overlay over accepted notes.

    A completed source can keep accepted annotations in its notes.  Formal
    infographic projection may alter only explicit generated visual fields and
    generated source sections; all other note bytes stay from the accepted
    current note.  Missing/ambiguous generated structure fails closed.
    """
    def normalized(raw: bytes) -> tuple[str, str, bytes]:
        bom = b"\xef\xbb\xbf" if raw.startswith(b"\xef\xbb\xbf") else b""
        text = raw.decode("utf-8-sig")
        newline = "\r\n" if "\r\n" in text else "\n"
        if "\r" in text.replace("\r\n", ""):
            raise ValueError("infographic overlay refuses mixed or bare carriage-return newlines")
        return text.replace("\r\n", "\n"), newline, bom

    source_text, source_newline, source_bom = normalized(source_before)
    application_text, application_newline, application_bom = normalized(application_before)
    source_rendered = source_rendered.replace("\r\n", "\n")
    application_rendered = application_rendered.replace("\r\n", "\n")
    source = _replace_markdown_sections(
        _replace_frontmatter_fields(source_text, source_rendered, INFOGRAPHIC_SOURCE_OVERLAY_FIELDS),
        source_rendered,
        INFOGRAPHIC_SOURCE_OVERLAY_SECTIONS,
    )
    application = _replace_frontmatter_fields(
        application_text, application_rendered, INFOGRAPHIC_APPLICATION_OVERLAY_FIELDS,
    )
    if extract_canonical_body_markdown(source) != extract_canonical_body_markdown(source_text):
        raise ValueError("infographic overlay changed retained source body")
    return (
        source_bom + source.replace("\n", source_newline).encode("utf-8"),
        application_bom + application.replace("\n", application_newline).encode("utf-8"),
    )


def apply_reviewed_content_projection(root: Path, vault: Path, items: list[dict], *, source_id: str) -> None:
    """The unique writer's same-path CAS for a reviewed content revision.

    All files are checked before the first write. A transaction may resume only
    from its exact before/after bytes. Input/assets and handwritten concepts
    are outside this bounded projection writer.
    """
    assert_management_idle_for_vault(vault)
    with vault_write_lock(vault):
        writes = []
        for item in items:
            target, candidate = root / item["path"], root / item["candidate_path"]
            if not target.resolve().is_relative_to(vault.resolve()):
                raise ValueError("content projection target escapes Vault")
            if not candidate.resolve().is_relative_to(root.resolve()) or not candidate.is_file():
                raise ValueError("content projection candidate escapes workspace")
            for component in (target, *target.parents):
                if component == vault: break
                if component.is_symlink() or (hasattr(component, "is_junction") and component.is_junction()):
                    raise ValueError("content projection target contains a link")
            after = candidate.read_bytes()
            before = target.read_bytes() if target.exists() else b""
            before_sha = hashlib.sha256(before).hexdigest()
            absent_asset = item.get("projection_kind") == "infographic_asset" and item.get("before_sha256") == ""
            before_matches = (absent_asset and not target.exists()) or before_sha in {item["before_sha256"], item["after_sha256"]}
            if hashlib.sha256(after).hexdigest() != item["after_sha256"] or not before_matches:
                raise ValueError("content projection changed since review")
            relative = target.relative_to(vault).as_posix()
            if target.suffix == ".md":
                if not relative.startswith(("sources/", "applications/", f"knowledge-points/by-source/{source_id}/", "concepts/")):
                    raise ValueError("unrelated note is outside content revision")
                if relative.startswith("concepts/") and target.exists() and not is_generated_concept_note(target):
                    raise ValueError("handwritten concept is not amendable")
                if relative.startswith("sources/"):
                    # Generated Markdown permits CRLF/LF readback; raw CAS and
                    # retained prose (including BOM presence) stay protected.
                    before_body = extract_canonical_body_markdown(before.decode("utf-8").replace("\r\n", "\n"))
                    after_body = extract_canonical_body_markdown(after.decode("utf-8").replace("\r\n", "\n"))
                    if before.startswith(b"\xef\xbb\xbf") != after.startswith(b"\xef\xbb\xbf") or before_body != after_body:
                        raise ValueError("content revision cannot change retained original body")
            elif item.get("projection_kind") == "infographic_asset":
                expected_prefix = f"assets/{source_id.lower()}/"
                if not relative.startswith(expected_prefix) or target.suffix.lower() != ".png":
                    raise ValueError("infographic asset target is outside the source asset directory")
                if item.get("before_sha256") == "":
                    if target.exists() and before_sha != item["after_sha256"]:
                        raise ValueError("infographic asset unexpectedly exists")
                elif not target.exists():
                    raise ValueError("infographic asset expected an existing CAS target")
            elif relative == ".obsidian/graph.json":
                prior_config = json.loads(before)
                after_config = json.loads(after)
                expected = dict(prior_config)
                if expected.get("search") == "path:concepts":
                    expected["search"] = "path:concepts -[concept_status:superseded]"
                if after_config != expected:
                    raise ValueError("content revision changed personal graph preferences")
            elif relative not in {"_registry/sources.jsonl", "_registry/knowledge_points.jsonl", "_registry/relations.jsonl"}:
                raise ValueError("unsupported content projection target")
            else:
                old_rows = [json.loads(line) for line in before.decode("utf-8-sig").splitlines() if line.strip()]
                new_rows = [json.loads(line) for line in after.decode("utf-8-sig").splitlines() if line.strip()]
                if [row for row in old_rows if row.get("source_id") != source_id] != [row for row in new_rows if row.get("source_id") != source_id]:
                    raise ValueError("content revision changed unrelated registry rows")
            if before != after:
                writes.append((target, after))
        for target, after in writes:
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_name("." + target.name + ".content-revision.tmp")
            if temporary.exists():
                if temporary.read_bytes() != after: raise ValueError("unowned projection temporary file")
            else:
                with temporary.open("xb") as stream: stream.write(after)
            os.replace(temporary, target)
            if target.read_bytes() != after:
                raise ValueError("content projection readback failed")


def write_graph_settings(vault: Path) -> None:
    assert_management_idle_for_vault(vault)
    ensure_initial_graph_preset(vault)


def project_concept_graph_for_sources(vault: Path, source_ids: set[str]) -> dict[str, object]:
    """Finish a source-local intake with its bounded concept-graph projection.

    It only writes concept nodes contributed by the completed source.  Existing
    user graph preferences are preserved rather than replaced.
    """
    assert_management_idle_for_vault(vault)
    selected_source_ids = sorted({source_id.strip() for source_id in source_ids if source_id.strip()})
    graph_action = ensure_initial_graph_preset(vault)
    source_kp_files: list[Path] = []
    for kp_file in sorted((vault / "knowledge-points" / "by-source").glob("*/*.md")):
        fields = frontmatter_fields(kp_file)
        if fields.get("type") == "knowledge_point" and knowledge_point_source_id(fields) in selected_source_ids:
            source_kp_files.append(kp_file)

    legacy_paths: list[Path] = []
    concept_paths = write_concept_notes(vault, source_ids=set(selected_source_ids), legacy_touched=legacy_paths) if source_kp_files else []
    graph_file = vault / ".obsidian" / "graph.json"
    graph_config_valid = False
    if graph_file.exists():
        try:
            json.loads(graph_file.read_text(encoding="utf-8-sig"))
            graph_config_valid = True
        except json.JSONDecodeError:
            graph_config_valid = False
    graph_status = graph_settings_status(vault)
    valid_groups = {"concept", "hub", "tension", "taste_signal", "application_scene"}
    concept_fields = [frontmatter_fields(path) for path in concept_paths if path.exists()]
    verification = {
        "source_has_knowledge_points": bool(source_kp_files),
        "affected_concept_notes_exist": len(concept_fields) == len(concept_paths) and all(path.exists() for path in legacy_paths),
        "affected_concept_notes_have_visual_groups": all(
            str(fields.get("graph_group") or "") in valid_groups and fields.get("idea_type") == "concept"
            for fields in concept_fields
        ),
        "graph_config_valid": graph_config_valid,
    }
    verification["passed"] = all(verification.values())
    return {
        "status": "ready" if verification["passed"] else "needs_review",
        "source_ids": selected_source_ids,
        "concept_notes": [path.relative_to(vault).as_posix() for path in sorted([*concept_paths, *legacy_paths])],
        "graph_settings": {
            "action": graph_action,
            "default_preset_active": bool(graph_status.get("ready")),
            "custom_settings_preserved": graph_action == "preserved_existing" and graph_config_valid and not bool(graph_status.get("ready")),
        },
        "verification": verification,
    }


def remove_machine_wikilinks_from_record_layer(root: Path, vault: Path) -> list[Path]:
    touched: list[Path] = []
    for folder in ("sources", "applications"):
        folder_root = vault / folder
        if not folder_root.exists():
            continue
        for path in sorted(folder_root.rglob("*.md")):
            text = path.read_text(encoding="utf-8-sig")
            cleaned = MACHINE_ID_WIKILINK_RE.sub(lambda match: match.group(1), text)
            if cleaned != text:
                path.write_text(cleaned, encoding="utf-8")
                touched.append(path)
    return touched + remove_machine_wikilinks_from_record_notes(root, vault)


def remove_machine_wikilinks_from_record_notes(root: Path, vault: Path) -> list[Path]:
    """The same cleanup for the knowledge-point notes, through the one note writer.  A note of a completed v2 source (or of a source whose
    completion cannot be resolved) is left alone: its receipt keeps its bytes."""
    notes_root = vault / "knowledge-points"
    if not notes_root.exists():
        return []
    module = completion_revisions_module()
    base = Path(root).resolve()
    if vault.resolve() != managed_vault_path(base):
        return []
    by_source: dict[str, dict[Path, bytes]] = {}
    for path in sorted(notes_root.rglob("*.md")):
        if len(path.relative_to(vault).parts) != 4:
            continue
        raw = path.read_bytes()
        bom = raw.startswith(b"\xef\xbb\xbf")
        text = (raw[3:] if bom else raw).decode("utf-8")
        cleaned = MACHINE_ID_WIKILINK_RE.sub(lambda match: match.group(1), text)
        if cleaned != text:
            by_source.setdefault(path.parent.name, {})[path] = (b"\xef\xbb\xbf" if bom else b"") + cleaned.encode("utf-8")
    changes: dict[Path, bytes] = {}
    for group in by_source.values():
        try:
            if any(owner["completed"] for owner in module.classify_kp_notes(base, list(group))):
                continue
        except module.CompletedNoteError:
            continue
        changes.update(group)
    if changes:
        module.rewrite_kp_notes(base, changes, reason="machine ID wikilinks removed from the record layer")
    return list(changes)


def backfill_application_confirmation_layer(vault: Path) -> list[Path]:
    root = vault / "applications"
    if not root.exists():
        return []
    touched: list[Path] = []
    for path in sorted(root.rglob("*.md")):
        text = path.read_text(encoding="utf-8-sig")
        updated = text
        ai_status = (
            frontmatter_scalar(updated, "ai_adoption_status")
            or frontmatter_scalar(updated, "ai_adoption_draft_status")
            or DEFAULT_AI_ADOPTION_STATUS
        )
        ai_reason = (
            frontmatter_scalar(updated, "ai_adoption_reason")
            or frontmatter_scalar(updated, "ai_adoption_draft_reason")
            or DEFAULT_AI_ADOPTION_REASON
        )
        if 'adoption_status: "暫存"\n' in updated or 'adoption_status: "未確認"\n' in updated:
            updated = re.sub(
                r'adoption_status: "(?:暫存|未確認)"\n',
                f'adoption_status: "{ai_status}"\n',
                updated,
                count=1,
            )
        updated = updated.replace(
            'adoption_reason: "待 使用者 判斷"\n',
            f'adoption_reason: "{ai_reason}"\n',
        )
        updated = updated.replace(
            'adoption_reason: "AI 已先給採納建議；使用者 只需確認、改判或稍後處理。"\n',
            f'adoption_reason: "{ai_reason}"\n',
        )
        updated = updated.replace(
            '  - "覆核採納狀態與一句原因"\n',
            '  - "只有不同意 AI 判斷時才手動修正 status"\n',
        )
        updated = updated.replace(
            '  - "確認是否需要加入個人化扣連或後續行動"\n',
            '  - "修正時直接更新 adoption_status / taste_link_status / personalization_status"\n',
        )
        updated = updated.replace(
            '  - "只需最後確認 AI 建議是否採納"\n',
            '  - "只有不同意 AI 判斷時才手動修正 status"\n',
        )
        updated = updated.replace(
            '  - "如不同意，改判為收藏 / 再訪 / 試行 / 深讀 / 暫存 / 拒絕"\n',
            '  - "修正時直接更新 adoption_status / taste_link_status / personalization_status"\n',
        )
        fields = frontmatter_fields_from_text(updated)
        if "ai_review_confidence" not in fields:
            updated = insert_frontmatter_field(updated, "ai_review_confidence", "中")
        if "manual_correction_status" not in fields:
            updated = insert_frontmatter_field(updated, "manual_correction_status", DEFAULT_MANUAL_CORRECTION_STATUS)
        if "manual_correction_action" not in fields:
            updated = insert_frontmatter_field(updated, "manual_correction_action", DEFAULT_MANUAL_CORRECTION_ACTION)
        if "ai_adoption_status" not in fields:
            updated = insert_frontmatter_field(updated, "ai_adoption_status", ai_status)
        if "ai_adoption_reason" not in fields:
            updated = insert_frontmatter_field(updated, "ai_adoption_reason", ai_reason)
        updated = re.sub(r'ai_adoption_draft_status: ".*"\n', "", updated)
        updated = re.sub(r'ai_adoption_draft_reason: ".*"\n', "", updated)
        updated = re.sub(r'human_confirmation_status: "未確認"\n', "", updated)
        updated = re.sub(r'human_confirmation_action: "確認AI建議 / 改判 / 稍後"\n', "", updated)
        updated = updated.replace('application_status: "draft"\n', 'application_status: "active"\n')
        updated = normalize_application_reader_residue(updated)
        source_reader_residue = application_reader_residue_terms(updated)
        if not source_reader_residue:
            updated = upsert_frontmatter_scalars(updated, application_overview_updates(vault, path, updated))
        compounding_defaults: dict[str, str] = {}
        current_fields = frontmatter_fields_from_text(updated)
        if "compounding_insight_status" not in current_fields:
            compounding_defaults["compounding_insight_status"] = DEFAULT_COMPOUNDING_STATUS
        if "compounding_next_action" not in current_fields:
            compounding_defaults["compounding_next_action"] = DEFAULT_COMPOUNDING_NEXT_ACTION
        if compounding_defaults:
            updated = upsert_frontmatter_scalars(updated, compounding_defaults)
        updated = ensure_frontmatter_empty_list_field(updated, "compounding_insight_types")
        updated = ensure_frontmatter_empty_list_field(updated, "compounding_insight_links")
        updated = updated.replace("## 採納判斷\n", "## 人工修正\n")
        updated = updated.replace("## 最後確認\n", "## 人工修正\n")
        updated = updated.replace("## AI 採納草案\n", "## AI 採納判斷\n")
        updated = updated.replace("- 狀態:未確認\n", "- 狀態:無修正\n")
        updated = updated.replace(
            "- 一句原因:待 使用者 判斷\n",
            "- 可做動作:不同意 AI 判斷時才改 status\n",
        )
        updated = updated.replace(
            "- 可做動作:確認AI建議 / 改判 / 稍後\n",
            "- 可做動作:不同意 AI 判斷時才改 status\n",
        )
        updated = updated.replace(
            "- 請由 使用者 覆核後改成:收藏 / 再訪 / 試行 / 深讀 / 暫存 / 拒絕,並保留一句原因。\n",
            "- 可做動作:不同意 AI 判斷時才改 status\n",
        )
        updated = updated.replace(
            "- 原則:AI 已先判斷;使用者 只需確認、改判或稍後處理。\n",
            "",
        )
        updated = updated.replace(
            "- 覆核採納狀態與一句原因。\n",
            "- 只有不同意 AI 判斷時才手動修正 status。\n",
        )
        updated = updated.replace(
            "- 覆核採納狀態與一句原因\n",
            "- 只有不同意 AI 判斷時才手動修正 status\n",
        )
        updated = updated.replace(
            "- 確認是否需要加入個人化扣連或後續行動\n",
            "- 修正時直接更新 adoption_status / taste_link_status / personalization_status\n",
        )
        updated = updated.replace(
            "- 只需最後確認 AI 建議是否採納。\n",
            "- 只有不同意 AI 判斷時才手動修正 status。\n",
        )
        updated = updated.replace(
            "- 只需最後確認 AI 建議是否採納\n",
            "- 只有不同意 AI 判斷時才手動修正 status\n",
        )
        updated = updated.replace(
            "- 如不同意，改判為收藏 / 再訪 / 試行 / 深讀 / 暫存 / 拒絕\n",
            "- 修正時直接更新 adoption_status / taste_link_status / personalization_status\n",
        )
        if "## 人工修正\n" not in updated:
            marker = "## AI 採納判斷\n"
            if marker in updated:
                updated = updated.replace(marker, "## 人工修正\n\n- 狀態:無修正\n- 可做動作:不同意 AI 判斷時才改 status\n\n" + marker, 1)
            else:
                ai_marker = "## AI 採納判斷\n"
                taste_marker = "## 品味網判斷\n"
                manual_section = "## 人工修正\n\n- 狀態:無修正\n- 可做動作:不同意 AI 判斷時才改 status\n\n"
                if ai_marker in updated and taste_marker in updated:
                    updated = updated.replace(taste_marker, manual_section + taste_marker, 1)
        if "## 跨來源洞見判斷\n" not in updated:
            default_categories = "".join(
                f"- {label}:{DEFAULT_COMPOUNDING_CONTEXT_JUDGMENTS[label]}\n"
                for label in VALID_COMPOUNDING_TYPES
            )
            section = (
                "\n## 跨來源洞見判斷\n\n"
                f"- 狀態:{DEFAULT_COMPOUNDING_STATUS}\n"
                f"- 下一步:{DEFAULT_COMPOUNDING_NEXT_ACTION}\n"
                + default_categories
            )
            marker = "\n## 仍需人工驗證事項\n"
            if marker in updated:
                updated = updated.replace(marker, section + marker, 1)
            else:
                updated = updated.rstrip() + section + "\n"
        if updated != text:
            path.write_text(updated, encoding="utf-8")
            touched.append(path)
    return touched


def application_overview_updates(vault: Path, application_path: Path, text: str) -> dict[str, str]:
    fields = frontmatter_fields_from_text(text)
    source_id = str(fields.get("source_id") or "").strip()
    source_record = str(fields.get("source_note") or "").strip()
    title = str(fields.get("title") or "").strip()
    adoption_status = frontmatter_scalar(text, "adoption_status") or frontmatter_scalar(text, "ai_adoption_status")
    ai_confidence = frontmatter_scalar(text, "ai_review_confidence")
    taste_status = frontmatter_scalar(text, "taste_network_status")
    taste_link_status = frontmatter_scalar(text, "taste_link_status")
    manual_status = frontmatter_scalar(text, "manual_correction_status") or DEFAULT_MANUAL_CORRECTION_STATUS
    source_path = find_vault_note_by_stem(vault / "sources", source_record) if source_record else None
    png_entry = ""
    if source_path:
        source_fields = frontmatter_fields(source_path)
        infographics = source_fields.get("infographics")
        if isinstance(infographics, list) and infographics:
            png_entry = str(infographics[0])
    missing: list[str] = []
    if not source_path:
        missing.append("缺少來源筆記")
    if not png_entry:
        missing.append("缺少 PNG")
    if not source_id:
        missing.append("缺少 source_id")
    issue_reason = "無" if not missing else "；".join(missing)
    ingest_status = "完整" if issue_reason == "無" else "需檢查"
    next_action = overview_next_action(
        adoption_status,
        taste_link_status,
        issue_reason == "無",
    )
    return {
        "ingest_status": ingest_status,
        "next_action": next_action,
        "issue_reason": issue_reason,
        "png_entry": png_entry,
        "source_entry": vault_relative(source_path) if source_path else "",
        "application_entry": vault_relative(application_path),
        "knowledge_points_entry": f"knowledge-points/by-source/{source_id}" if source_id else "",
        "項目": concise_overview_title(title),
        "入庫": ingest_status,
        "AI總判斷": adoption_status,
        "AI信心": ai_confidence,
        "下一步": next_action,
        "品味": taste_status,
        "扣連": taste_link_label(taste_link_status),
        "問題": issue_reason,
        "修正": manual_status,
        "PNG": png_entry,
        "原文": vault_relative(source_path) if source_path else "",
        "應用": vault_relative(application_path),
        "知識點": f"knowledge-points/by-source/{source_id}" if source_id else "",
    }


def concise_overview_title(title: str) -> str:
    cleaned = title.replace(" — 個人應用", "").strip()
    cleaned = cleaned.replace("Anthropic Claude Platform Docs — ", "Claude Docs — ")
    cleaned = cleaned.replace("Anthropic Engineering — ", "Anthropic — ")
    cleaned = cleaned.replace("Obsidian Help — ", "Obsidian — ")
    return cleaned


def taste_link_label(status: object) -> str:
    value = str(status or "")
    labels = {
        "linked": "已扣連",
        "taste_needs_context": "需脈絡",
        "not_applicable": "不適用",
        "blocked": "受阻",
    }
    return labels.get(value, value or "未判斷")


def overview_next_action(
    adoption_status: str,
    taste_link_status: str,
    complete: bool,
    *,
    has_infographic: bool = True,
) -> str:
    if not complete:
        return "先處理入庫缺口"
    if taste_link_status == "linked":
        return "看 PNG，再讀應用筆記" if has_infographic else "再讀應用筆記"
    if taste_link_status == "taste_needs_context":
        return "看 PNG，再補品味脈絡" if has_infographic else "補品味脈絡"
    if adoption_status in {"深讀", "試行", "收藏"}:
        return "看 PNG，再讀原文或知識點" if has_infographic else "再讀原文或知識點"
    if adoption_status in {"暫存", "再訪"}:
        return "看 PNG，暫不深讀" if has_infographic else "暫不深讀"
    if adoption_status == "拒絕":
        return "無需處理"
    return "看 PNG，再決定是否打開" if has_infographic else "再決定是否打開"


def find_vault_note_by_stem(root: Path, stem: str) -> Path | None:
    if not root.exists():
        return None
    for path in sorted(root.rglob(f"{stem}.md")):
        return path
    return None


def insert_frontmatter_field(text: str, key: str, value: str) -> str:
    if not text.startswith("---\n"):
        return text
    marker = "\n---\n"
    end = text.find(marker, 4)
    if end == -1:
        return text
    return text[:end] + f"\n{key}: {yaml_scalar(value)}" + text[end:]


def ensure_frontmatter_empty_list_field(text: str, key: str) -> str:
    if key in frontmatter_fields_from_text(text):
        return text
    if not text.startswith("---\n"):
        return text
    marker = "\n---\n"
    end = text.find(marker, 4)
    if end == -1:
        return text
    return text[:end] + f"\n{key}: []" + text[end:]


def assert_source_id_is_available(vault: Path, source_id_value: str, source_identity_value: str) -> None:
    """Check the source-ID namespace while the sole vault writer holds its lock."""
    for existing_identity, row in read_source_registry(vault).items():
        if existing_identity != source_identity_value and row.get("source_id") == source_id_value:
            raise AdapterStop(
                "blocked",
                f"source_id {source_id_value} is already linked to a different source identity",
            )


def rebind_uncompleted_case_source_id(
    vault: Path,
    *,
    source_identity_value: str,
    old_source_id: str,
    new_source_id: str,
    source_note: Path,
    application_note: Path,
    knowledge_points: list[Path],
    case_asset_dir: Path,
    artifact_hashes: dict[str, str] | None = None,
) -> dict[str, object]:
    """Preserve an incomplete case while correcting a discovered source-ID collision.

    This deliberately moves only the exact artifacts named by the locked job,
    removes only that identity's registry records, and refuses any path outside
    the selected vault.  A retry resumes a recorded partial move only when its
    destination hashes still match the checkpoint.
    """
    vault = vault.resolve()
    if not source_identity_value or old_source_id == new_source_id:
        raise AdapterStop("needs_review", "source-ID rebind requires distinct source identity and IDs")

    def inside(path: Path, label: str) -> Path:
        resolved = path.resolve()
        if not resolved.is_relative_to(vault):
            raise AdapterStop("needs_review", f"source-ID rebind {label} escapes vault")
        return resolved

    source_note = inside(source_note, "source note")
    application_note = inside(application_note, "application note")
    case_asset_dir = inside(case_asset_dir, "case asset directory")
    knowledge_points = [inside(path, "knowledge point") for path in knowledge_points]
    expected = [source_note, application_note, *knowledge_points, case_asset_dir]
    if not source_note.name.startswith(old_source_id) or not application_note.name.startswith(f"APP-{old_source_id}"):
        raise AdapterStop("needs_review", "source-ID rebind paths do not match the locked source ID")
    if case_asset_dir.name != "case-evidence" or case_asset_dir.parent.name != old_source_id.lower():
        raise AdapterStop("needs_review", "source-ID rebind case assets do not match the locked source ID")

    source_destination = source_note.with_name(source_note.name.replace(old_source_id, new_source_id, 1))
    application_destination = application_note.with_name(application_note.name.replace(old_source_id, new_source_id, 1))
    kp_destinations: list[Path] = []
    for path in knowledge_points:
        old_kp_fragment = old_source_id.removeprefix("KS-")
        new_kp_fragment = new_source_id.removeprefix("KS-")
        if old_source_id not in path.parts or old_kp_fragment not in path.name:
            raise AdapterStop("needs_review", "source-ID rebind knowledge-point path does not match the locked source ID")
        parent_parts = [new_source_id if part == old_source_id else part for part in path.parent.parts]
        destination = Path(*parent_parts) / path.name.replace(old_kp_fragment, new_kp_fragment, 1)
        kp_destinations.append(inside(destination, "new knowledge point"))
    asset_destination = vault / "assets" / new_source_id.lower() / "case-evidence"
    destinations = [source_destination, application_destination, *kp_destinations, asset_destination]
    expected_hashes = artifact_hashes or {}

    def moved_artifact_has_expected_hash(source: Path, destination: Path) -> bool:
        source_relative = source.relative_to(vault).as_posix()
        if source_relative in expected_hashes:
            expected_hash = expected_hashes[source_relative]
            return bool(expected_hash) and destination.is_file() and hashlib.sha256(destination.read_bytes()).hexdigest() == expected_hash
        prefix = source_relative.rstrip("/") + "/"
        directory_entries = [
            (relative[len(prefix):], expected_hash)
            for relative, expected_hash in expected_hashes.items()
            if relative.startswith(prefix) and expected_hash
        ]
        return bool(directory_entries) and all(
            (destination / relative).is_file()
            and hashlib.sha256((destination / relative).read_bytes()).hexdigest() == expected_hash
            for relative, expected_hash in directory_entries
        )

    with vault_write_lock(vault):
        registry = read_source_registry(vault)
        current = registry.get(source_identity_value)
        states: list[str] = []
        for source, destination in zip(expected, destinations):
            if source.exists() and destination.exists():
                raise AdapterStop("needs_review", "source-ID rebind found both old and new artifact paths")
            if source.exists():
                states.append("old")
            elif destination.exists() and moved_artifact_has_expected_hash(source, destination):
                states.append("new")
            elif destination.exists():
                raise AdapterStop("needs_review", "source-ID rebind moved artifact hash does not match its checkpoint")
            else:
                raise AdapterStop("needs_review", "source-ID rebind is missing a recorded partial artifact")
        if all(state == "new" for state in states) and (current is None or current.get("source_id") == new_source_id):
            return {
                "status": "already_rebound",
                "old_source_id": old_source_id,
                "new_source_id": new_source_id,
                "source_registry_rows_removed": 0,
                "knowledge_point_registry_rows_removed": 0,
                "relation_registry_rows_removed": 0,
                "moved": [],
            }
        if current and current.get("source_id") not in {old_source_id, new_source_id}:
            raise AdapterStop("needs_review", "source-ID rebind registry no longer matches the incomplete job")
        if current and current.get("source_id") == new_source_id:
            raise AdapterStop("needs_review", "source-ID rebind registry is already linked to the new ID before artifact recovery")
        collisions = [
            row for identity, row in registry.items()
            if identity != source_identity_value and row.get("source_id") == new_source_id
        ]
        if collisions:
            raise AdapterStop("needs_review", "source-ID rebind new ID is already used")
        registry_paths = (
            source_registry_file(vault),
            knowledge_point_registry_file(vault),
            relation_registry_file(vault),
        )
        registry_before = {path: path.read_bytes() if path.exists() else None for path in registry_paths}
        moved_paths: list[tuple[Path, Path]] = []
        try:
            registry.pop(source_identity_value, None)
            source_registry_file(vault).write_text(
                "\n".join(json.dumps(row, ensure_ascii=False, sort_keys=True) for _, row in sorted(registry.items())) + "\n",
                encoding="utf-8",
            )

            kp_rows = read_knowledge_point_registry(vault)
            removed_kps = [key for key, row in kp_rows.items() if row.get("source_identity") == source_identity_value]
            for key in removed_kps:
                kp_rows.pop(key)
            write_knowledge_point_registry_rows(vault, kp_rows)

            relation_rows = read_relation_registry(vault)
            removed_relations = [key for key, row in relation_rows.items() if row.get("source_identity") == source_identity_value]
            for key in removed_relations:
                relation_rows.pop(key)
            relation_registry_file(vault).write_text(
                "\n".join(json.dumps(row, ensure_ascii=False, sort_keys=True) for _, row in sorted(relation_rows.items()))
                + ("\n" if relation_rows else ""),
                encoding="utf-8",
            )

            for source, destination, state in zip(expected, destinations, states):
                if state == "new":
                    continue
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(source), str(destination))
                moved_paths.append((source, destination))
        except Exception as exc:
            restore_errors: list[str] = []
            for source, destination in reversed(moved_paths):
                try:
                    if destination.exists() and not source.exists():
                        source.parent.mkdir(parents=True, exist_ok=True)
                        shutil.move(str(destination), str(source))
                except Exception as restore_exc:  # pragma: no cover - requires OS write failure.
                    restore_errors.append(str(restore_exc))
            for path, payload in registry_before.items():
                try:
                    if payload is None:
                        path.unlink(missing_ok=True)
                    else:
                        path.parent.mkdir(parents=True, exist_ok=True)
                        path.write_bytes(payload)
                except Exception as restore_exc:  # pragma: no cover - requires OS write failure.
                    restore_errors.append(str(restore_exc))
            if restore_errors:
                raise AdapterStop("needs_review", "source-ID rebind failed and its rollback is incomplete") from exc
            raise AdapterStop("blocked", "source-ID rebind failed; registry and partial artifacts were restored") from exc

    moved = [
        {"from": source.relative_to(vault).as_posix(), "to": destination.relative_to(vault).as_posix()}
        for source, destination in moved_paths
    ]
    return {
        "status": "rebound",
        "old_source_id": old_source_id,
        "new_source_id": new_source_id,
        "source_registry_rows_removed": 1,
        "knowledge_point_registry_rows_removed": len(removed_kps),
        "relation_registry_rows_removed": len(removed_relations),
        "moved": moved,
        "resumed_partial_move": any(state == "new" for state in states),
    }


def write_plan(plan: dict[str, object], *, source_local: bool = False) -> None:
    registry_issue = plan["registry_issue"]
    if registry_issue:
        raise AdapterStop("needs_review", str(registry_issue))

    vault = plan["vault"]
    assert isinstance(vault, Path)
    targets = [plan.get(key) for key in ("source_note", "application_note", "source_image_dest", "registry_file", "knowledge_point_registry_file", "relation_registry_file")]
    targets += (
        list(plan.get("knowledge_point_notes", []))
        + list(plan.get("asset_dests", []))
        + list(plan.get("case_asset_dests", []))
        + list(plan.get("base_files", []))
    )
    targets += [vault / name for name in ("_registry", "sources", "knowledge-points", "applications", "assets", "bases", "concepts")]
    if not vault.resolve().is_relative_to(Path(plan["root"]).resolve()) or any(
        isinstance(target, Path) and not target.resolve().is_relative_to(vault.resolve()) for target in targets
    ):
        raise AdapterStop("blocked", "resolved write target escapes the selected Vault")
    with vault_write_lock(vault):
        data = plan["data"]
        assert isinstance(data, SliceData)
        identity = str(plan["source_identity"])
        assert_source_id_is_available(vault, data.source_id, identity)
        directories = ("_registry", "sources", "knowledge-points", "applications", "assets")
        if not source_local:
            directories += ("bases", "concepts")
        for dirname in directories:
            (vault / dirname).mkdir(parents=True, exist_ok=True)

        asset_srcs = plan.get("asset_srcs", [])
        asset_dests = plan.get("asset_dests", [])
        for asset_src, asset_dest in zip(asset_srcs, asset_dests):
            if isinstance(asset_src, Path) and isinstance(asset_dest, Path):
                asset_dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(asset_src, asset_dest)

        for asset_src, asset_dest in zip(plan.get("case_asset_srcs", []), plan.get("case_asset_dests", [])):
            if isinstance(asset_src, Path) and isinstance(asset_dest, Path):
                asset_dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(asset_src, asset_dest)

        source_image_src = plan.get("source_image_src")
        source_image_dest = plan.get("source_image_dest")
        if isinstance(source_image_src, Path) and isinstance(source_image_dest, Path):
            source_image_dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_image_src, source_image_dest)

        source_note = plan["source_note"]
        assert isinstance(source_note, Path)
        source_note.parent.mkdir(parents=True, exist_ok=True)
        source_note.write_text(plan["source_body"], encoding="utf-8")

        for path, body in zip(plan["knowledge_point_notes"], plan["knowledge_point_bodies"]):
            assert isinstance(path, Path)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(body, encoding="utf-8")

        application_note = plan["application_note"]
        assert isinstance(application_note, Path)
        application_note.parent.mkdir(parents=True, exist_ok=True)
        application_note.write_text(plan["application_body"], encoding="utf-8")

        write_source_registry(plan)
        write_knowledge_point_registry(plan)
        write_relation_registry(plan)
        if not source_local:
            write_base_files(vault)
            write_concept_notes(vault)
            backfill_application_confirmation_layer(vault)
            root = plan["root"]
            assert isinstance(root, Path)
            remove_machine_wikilinks_from_record_layer(root, vault)
            write_graph_settings(vault)


@contextmanager
def vault_write_lock(vault: Path):
    assert_management_idle_for_vault(vault)
    lock_dir = vault / "_registry"
    lock_dir.mkdir(parents=True, exist_ok=True)
    lock_file = lock_dir / ".write.lock"
    try:
        handle = os.open(lock_file, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        raise AdapterStop(
            "blocked",
            f"vault write lock exists at {vault_relative(lock_file)}; finish or inspect the active writer before retrying",
        ) from exc
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(f"pid={os.getpid()}\n")
        yield
    finally:
        try:
            lock_file.unlink()
        except FileNotFoundError:
            pass


def write_source_registry(plan: dict[str, object], *, render_only: bool = False) -> bytes | None:
    vault = plan["vault"]
    data = plan["data"]
    source_note = plan["source_note"]
    registry_file = plan["registry_file"]
    identity = plan["source_identity"]
    assert isinstance(vault, Path)
    assert isinstance(data, SliceData)
    assert isinstance(source_note, Path)
    assert isinstance(registry_file, Path)
    assert isinstance(identity, str)

    if not render_only:
        registry_file.parent.mkdir(parents=True, exist_ok=True)
    existing = read_source_registry(vault)
    row = {
        "source_identity": identity,
        "source_input_kind": data.source_input_kind,
        "source_retention_mode": data.source_retention_mode,
        "source_id": data.source_id,
        "source_note_path": vault_relative(source_note),
        "status": "linked",
        "updated": str(date.today()),
    }
    if data.source_input_kind == "url":
        row["source_url"] = data.source_url
        row["source_url_hash"] = source_url_hash(data.source_url)
    elif data.source_input_kind == "user_provided_text":
        row["source_content_sha256"] = data.source_content_sha256
    else:
        row["source_image_sha256"] = data.source_image_sha256
        row["source_image_mime"] = data.source_image_mime
        row["source_image_width"] = str(data.source_image_width)
        row["source_image_height"] = str(data.source_image_height)
    if data.source_retention_mode in CANONICAL_HASH_RETENTION_MODES:
        row["canonical_body_sha256"] = data.canonical_body_sha256
    existing[identity] = row
    rows = [existing[key] for key in sorted(existing)]
    if render_only:
        return ("\n".join(json.dumps(row, ensure_ascii=False, sort_keys=True) for row in rows) + "\n").encode("utf-8")
    registry_file.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False, sort_keys=True) for row in rows) + "\n",
        encoding="utf-8",
    )


def write_knowledge_point_registry(plan: dict[str, object], *, render_only: bool = False) -> bytes | None:
    vault = plan["vault"]
    data = plan["data"]
    kp_notes = plan["knowledge_point_notes"]
    duplicate_of = plan["knowledge_point_duplicate_of"]
    identity = plan["source_identity"]
    assert isinstance(vault, Path)
    assert isinstance(data, SliceData)
    assert isinstance(identity, str)
    assert isinstance(duplicate_of, dict)
    registry_file = knowledge_point_registry_file(vault)
    if not render_only:
        registry_file.parent.mkdir(parents=True, exist_ok=True)
    existing = read_knowledge_point_registry(vault)
    for kp, note_path in zip(data.knowledge_points, kp_notes):
        assert isinstance(note_path, Path)
        uid = knowledge_point_uid(data.source_id, kp.kp_id)
        previous = existing.get(uid, {})
        pending_duplicate_of = str(duplicate_of.get(kp.kp_id, ""))
        reviewed_decision = previous.get("duplicate_review_decision", "")
        if reviewed_decision:
            row_duplicate_of = previous.get("duplicate_of", "")
            row_status = previous.get("status", "linked")
        else:
            row_duplicate_of = pending_duplicate_of
            row_status = "duplicate_candidate" if pending_duplicate_of else "linked"
        existing[uid] = {
            "kp_uid": uid,
            "kp_id": kp.kp_id,
            "source_id": data.source_id,
            "source_identity": identity,
            "kp_note_path": vault_relative(note_path),
            "semantic_key": knowledge_point_semantic_key(kp.text),
            "semantic_text": normalized_semantic_text(kp.text),
            "knowledge_point": kp.text,
            "duplicate_of": row_duplicate_of,
            "status": row_status,
            "updated": str(date.today()),
        }
        if kp.understanding_delta:
            existing[uid].update(
                concept_name=kp.reusable_concept_name,
                understanding_delta=kp.understanding_delta,
                job_id=data.prior_job_id,
            )
        for key in (
            "duplicate_review_decision",
            "duplicate_review_reason",
            "duplicate_reviewed_at",
            "duplicate_reviewed_by",
        ):
            if previous.get(key):
                existing[uid][key] = previous[key]
    rows = [existing[key] for key in sorted(existing)]
    if render_only:
        return ("\n".join(json.dumps(row, ensure_ascii=False, sort_keys=True) for row in rows) + "\n").encode("utf-8")
    registry_file.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False, sort_keys=True) for row in rows) + "\n",
        encoding="utf-8",
    )


def write_relation_registry(plan: dict[str, object], *, render_only: bool = False) -> bytes | None:
    vault = plan["vault"]
    data = plan["data"]
    source_note = plan["source_note"]
    identity = plan["source_identity"]
    relations = plan["relations"]
    assert isinstance(vault, Path)
    assert isinstance(data, SliceData)
    assert isinstance(source_note, Path)
    assert isinstance(identity, str)
    assert isinstance(relations, list)
    registry_file = relation_registry_file(vault)
    if not render_only:
        registry_file.parent.mkdir(parents=True, exist_ok=True)
    existing = read_relation_registry(vault)
    cross_links = plan.get("cross_source_links") or []
    if plan.get("content_revision"):
        active = {relation_id(data.source_id, relation) for relation in relations}
        active |= {cross_source_link_id(data.source_id, link) for link in cross_links}
        for rid, row in existing.items():
            if row.get("source_id") == data.source_id and rid not in active and row.get("status", "linked") == "linked":
                row.update(status="superseded", revision_reason="source-grounded content revision")
    for relation in relations:
        assert isinstance(relation, KnowledgeRelation)
        rid = relation_id(data.source_id, relation)
        # Replaying an old source plan cannot reinstate a reviewed withdrawal.
        if rid in existing and existing[rid].get('status', 'linked') != 'linked':
            continue
        existing[rid] = {
            "relation_id": rid,
            "source_id": data.source_id,
            "source_identity": identity,
            "source_note_path": vault_relative(source_note),
            "source_kp_uid": knowledge_point_uid(data.source_id, relation.source_kp_id),
            "target_kp_uid": knowledge_point_uid(data.source_id, relation.target_kp_id),
            "source_kp_id": relation.source_kp_id,
            "target_kp_id": relation.target_kp_id,
            "relation": relation.relation,
            "evidence": relation.evidence,
            "confidence": relation.confidence,
            "status": "linked",
            "updated": str(date.today()),
        }
    kp_registry = read_knowledge_point_registry(vault) if cross_links else {}
    for link in cross_links:
        assert isinstance(link, CrossSourceLink)
        target = kp_registry.get(link.target_kp_uid, {})
        if target.get("status") != "linked" or target.get("source_id") == data.source_id:
            raise AdapterStop("needs_review", f"cross-source link target is not a linked older knowledge point: {link.target_kp_uid}")
        source_uid = knowledge_point_uid(data.source_id, link.source_kp_id)
        upsert_cross_source_row(existing, cross_source_relation_row(
            source_id=data.source_id, source_identity=identity, source_note_path=vault_relative(source_note), source_kp_uid=source_uid,
            source_kp_id=link.source_kp_id, target_kp_uid=link.target_kp_uid, target_kp_id=target.get("kp_id", ""), relation=link.relation,
            evidence=link.evidence, confidence=link.confidence, target_source_id=target.get("source_id", ""), insight=link.insight,
            job_id=data.prior_job_id))
    rows = [existing[key] for key in sorted(existing)]
    if render_only:
        return registry_rows_text(rows).encode("utf-8")
    registry_file.write_text(registry_rows_text(rows), encoding="utf-8")


def write_base_files(vault: Path) -> None:
    assert_management_idle_for_vault(vault)
    (vault / "bases").mkdir(parents=True, exist_ok=True)
    legacy_daily_review = vault / "bases" / "30秒審閱台.base"
    if legacy_daily_review.exists():
        legacy_daily_review.unlink()
    (vault / "bases" / "sources-recent.base").write_text(
        """filters:
  and:
    - type == "source"
properties:
  title:
    displayName: 標題
  source_type:
    displayName: 來源類型
  knowledge_use:
    displayName: 知識用途
  summary:
    displayName: 短摘要
  status:
    displayName: 入庫狀態
  readability_status:
    displayName: 真源狀態
  fact_network_status:
    displayName: 事實網
  infographics:
    displayName: 資訊圖
  source_url:
    displayName: 來源 URL
views:
  - type: table
    name: 最近來源
    order:
      - title
      - source_type
      - knowledge_use
      - summary
      - status
      - readability_status
      - fact_network_status
      - infographics
      - source_url
""",
        encoding="utf-8",
    )
    (vault / "bases" / "sources-review.base").write_text(
        """filters:
  and:
    - type == "source"
    - status == "needs_review" or readability_status == "partial_capture" or readability_status == "source_blocked" or ai_source_analysis_status != "complete" or png_semantic_validation_status != "passed"
properties:
  title:
    displayName: 標題
  source_url:
    displayName: 來源 URL
  readability_status:
    displayName: 可讀性
  ai_source_analysis_status:
    displayName: AI 來源分析
  png_semantic_validation_status:
    displayName: PNG 語義驗收
  status:
    displayName: 入庫狀態
views:
  - type: table
    name: 來源待處理
    order:
      - file.name
      - title
      - source_url
      - readability_status
      - ai_source_analysis_status
      - png_semantic_validation_status
      - status
""",
        encoding="utf-8",
    )
    (vault / "bases" / "knowledge-points-by-source.base").write_text(
        """filters:
  and:
    - type == "knowledge_point"
properties:
  knowledge_point:
    displayName: 知識點
  source_note:
    displayName: 來源
  confidence:
    displayName: 信心
  review_status:
    displayName: 覆核
views:
  - type: table
    name: 知識點
    order:
      - knowledge_point
      - source_note
      - confidence
      - review_status
""",
        encoding="utf-8",
    )
    (vault / "bases" / "applications-review.base").write_text(
        """filters:
  and:
    - type == "application"
properties:
  application_id:
    displayName: ID
  title:
    displayName: 內容
  source_note:
    displayName: 來源
  ingest_status:
    displayName: 入庫狀態
  next_action:
    displayName: 下一步
  issue_reason:
    displayName: 問題原因
  png_entry:
    displayName: PNG
  source_entry:
    displayName: Source
  application_entry:
    displayName: Application
  knowledge_points_entry:
    displayName: 知識點
  personal_relevance:
    displayName: 個人相關
  taste_network_status:
    displayName: 進品味網
  taste_network_reason:
    displayName: 品味原因
  adoption_status:
    displayName: 目前採納
  adoption_reason:
    displayName: 目前理由
  manual_correction_status:
    displayName: 人工修正
  manual_correction_action:
    displayName: 修正入口
  ai_adoption_status:
    displayName: AI 原判
  ai_adoption_reason:
    displayName: AI 原因
  ai_review_confidence:
    displayName: AI 信心
  personalization_status:
    displayName: 個人扣連
  personalization_links:
    displayName: 個人扣連內容
  compounding_insight_status:
    displayName: 複利狀態
  compounding_insight_types:
    displayName: 複利類型
  compounding_insight_links:
    displayName: 複利扣連
  compounding_next_action:
    displayName: 複利下一步
  taste_link_status:
    displayName: 品味扣連
  taste_links:
    displayName: 品味扣連內容
  human_verification_items:
    displayName: 修正指引
  action_status:
    displayName: 下一步
  application_status:
    displayName: 應用狀態
views:
  - type: table
    name: 完整應用審核
    order:
      - file.name
      - application_id
      - title
      - source_note
      - ingest_status
      - next_action
      - issue_reason
      - png_entry
      - source_entry
      - application_entry
      - knowledge_points_entry
      - personal_relevance
      - taste_network_status
      - taste_network_reason
      - adoption_status
      - adoption_reason
      - manual_correction_status
      - manual_correction_action
      - ai_adoption_status
      - ai_adoption_reason
      - ai_review_confidence
      - personalization_status
      - personalization_links
      - compounding_insight_status
      - compounding_insight_types
      - compounding_insight_links
      - compounding_next_action
      - taste_link_status
      - taste_links
      - human_verification_items
      - action_status
      - application_status
""",
        encoding="utf-8",
    )
    (vault / "bases" / "入庫總覽.base").write_text(
        """filters:
  and:
    - type == "application"
properties:
  項目:
    displayName: 項目
  入庫:
    displayName: 入庫
  AI總判斷:
    displayName: AI總判斷
  AI信心:
    displayName: AI信心
  下一步:
    displayName: 下一步
  複利:
    displayName: 複利
  複利下一步:
    displayName: 複利下一步
  品味:
    displayName: 品味
  扣連:
    displayName: 扣連
  問題:
    displayName: 問題
  修正:
    displayName: 修正
  PNG:
    displayName: PNG
  原文:
    displayName: 原文
  應用:
    displayName: 應用
  知識點:
    displayName: 知識點
  title:
    displayName: 內容
  ingest_status:
    displayName: 入庫
  adoption_status:
    displayName: AI 總判斷
  ai_review_confidence:
    displayName: AI 信心
  next_action:
    displayName: 下一步
  png_entry:
    displayName: PNG
  source_entry:
    displayName: Source
  application_entry:
    displayName: Application
  knowledge_points_entry:
    displayName: 知識點
  taste_network_status:
    displayName: 進品味網
  taste_link_status:
    displayName: 品味扣連
  compounding_insight_status:
    displayName: 複利狀態
  compounding_next_action:
    displayName: 複利下一步
  issue_reason:
    displayName: 問題原因
  manual_correction_status:
    displayName: 人工修正
views:
  - type: table
    name: 入庫總覽
    order:
      - 項目
      - 入庫
      - AI總判斷
      - AI信心
      - 下一步
      - 複利
      - 複利下一步
      - 品味
      - 扣連
      - 問題
      - 修正
  - type: table
    name: 入口路徑
    order:
      - 項目
      - PNG
      - 原文
      - 應用
      - 知識點
""",
        encoding="utf-8",
    )
    (vault / "bases" / "品味探索.base").write_text(
        """filters:
  and:
    - type == "application"
    - taste_network_status == "是" or taste_link_status == "linked" or taste_link_status == "taste_needs_context"
properties:
  title:
    displayName: 內容
  taste_network_status:
    displayName: 進品味網
  taste_network_reason:
    displayName: 品味原因
  taste_link_status:
    displayName: 品味扣連
  taste_links:
    displayName: 扣連內容
  personalization_links:
    displayName: 個人扣連
  compounding_insight_status:
    displayName: 複利狀態
  compounding_insight_types:
    displayName: 複利類型
  compounding_next_action:
    displayName: 複利下一步
  source_note:
    displayName: 來源
views:
  - type: table
    name: 品味探索
    order:
      - title
      - taste_network_status
      - taste_network_reason
      - taste_link_status
      - taste_links
      - compounding_insight_status
      - compounding_insight_types
      - compounding_next_action
      - source_note
""",
        encoding="utf-8",
    )
    (vault / "bases" / "概念地圖.base").write_text(
        """filters:
  and:
    - type == "concept"
properties:
  concept:
    displayName: 概念
  graph_role:
    displayName: 圖中角色
  graph_question:
    displayName: 可追問
  related_concepts:
    displayName: 相關概念
views:
  - type: table
    name: 概念地圖
    order:
      - concept
      - graph_role
      - graph_question
      - related_concepts
""",
        encoding="utf-8",
    )
    (vault / "bases" / "maintenance.base").write_text(
        """filters:
  or:
    - status == "needs_review"
    - review_status == "需更多脈絡"
    - application_status == "draft"
    - manual_correction_status == "需修正"
    - ingest_status == "需檢查"
properties:
  type:
    displayName: Type
  status:
    displayName: Status
  review_status:
    displayName: Review
  application_status:
    displayName: Application status
  manual_correction_status:
    displayName: 人工修正
  issue_reason:
    displayName: 問題原因
  readability_status:
    displayName: 可讀性
  ai_source_analysis_status:
    displayName: AI 來源分析
  png_semantic_validation_status:
    displayName: PNG 語義驗收
  duplicate_of:
    displayName: 疑似重複
views:
  - type: table
    name: 維護隊列
    order:
      - file.name
      - type
      - status
      - review_status
      - application_status
      - manual_correction_status
      - issue_reason
      - readability_status
      - ai_source_analysis_status
      - png_semantic_validation_status
      - duplicate_of
""",
        encoding="utf-8",
    )


def verify_plan(plan: dict[str, object], *, source_local: bool = False) -> dict[str, object]:
    source_note = plan["source_note"]
    application_note = plan["application_note"]
    asset_dest = plan["asset_dest"]
    asset_dests = plan.get("asset_dests", [])
    case_asset_dests = plan.get("case_asset_dests", [])
    case_image_dests = plan.get("case_image_dests", [])
    source_image_dest = plan.get("source_image_dest")
    kp_notes = plan["knowledge_point_notes"]
    base_files = plan["base_files"]
    registry_file = plan["registry_file"]
    kp_registry_file = plan["knowledge_point_registry_file"]
    relation_registry_file = plan["relation_registry_file"]
    identity = plan["source_identity"]
    relations = plan["relations"]
    data = plan["data"]
    vault = plan["vault"]
    assert isinstance(source_note, Path)
    assert isinstance(application_note, Path)
    assert isinstance(registry_file, Path)
    assert isinstance(kp_registry_file, Path)
    assert isinstance(relation_registry_file, Path)
    assert isinstance(identity, str)
    assert isinstance(relations, list)
    assert isinstance(data, SliceData)
    assert isinstance(vault, Path)
    text = source_note.read_text(encoding="utf-8-sig")
    application_text = application_note.read_text(encoding="utf-8-sig") if application_note.exists() else ""
    registry = read_source_registry(vault)
    registry_row = registry.get(identity, {})
    registry_target = vault / str(registry_row.get("source_note_path", ""))
    kp_registry = read_knowledge_point_registry(vault)
    relation_registry = read_relation_registry(vault)
    enum_violations = source_note_enum_violations(vault, [source_note]) if source_note.exists() else []
    adoption_field_gaps = application_adoption_field_gaps(
        vault,
        [application_note] if application_note.exists() else [],
    )
    review_field_gaps = application_review_field_gaps(
        vault,
        [application_note] if application_note.exists() else [],
    )
    compounding_field_gaps = application_compounding_field_gaps(
        vault,
        [application_note] if application_note.exists() else [],
    )
    expected_kp_uids = [knowledge_point_uid(data.source_id, kp.kp_id) for kp in data.knowledge_points]
    # One entry per KP; None means the writer's analysis expects no concept note.
    expected_concept_pairs = [
        (kp_path, planned_concept_note_path(vault, kp, kp_path))
        for kp, kp_path in zip(data.knowledge_points, plan["knowledge_point_notes"])
    ]
    expected_concept_notes = [concept for _, concept in expected_concept_pairs if concept is not None]
    expected_relation_ids = [
        relation_id(data.source_id, relation)
        for relation in relations
        if isinstance(relation, KnowledgeRelation)
    ]
    note_canonical_span = canonical_source_section_span(text)
    actual_source_relations = parse_relation_rows(text[note_canonical_span[1]:] if note_canonical_span else text)
    expected_outgoing = relations_grouped_by_source(relations)
    kp_relation_projection_matches = True
    for kp, note in zip(data.knowledge_points, kp_notes):
        if not note.is_file():
            kp_relation_projection_matches = False
            continue
        note_text = note.read_text(encoding="utf-8-sig")
        actual_points = frontmatter_fields_from_text(note_text).get("related_points") or []
        if actual_points == "[]": actual_points = []
        outgoing = expected_outgoing.get(kp.kp_id, [])
        expected_points = [r.target_kp_id for r in outgoing]
        expected_lines = "\n".join(
            f"- {r.relation}: `{r.target_kp_id}` ({r.confidence}; {r.evidence})" for r in outgoing
        ) or "- 暫無已登記關係。"
        if actual_points != expected_points or section_after(note_text, "## Registered Relations").strip() != expected_lines:
            kp_relation_projection_matches = False
    canonical_body = extract_canonical_body_markdown(text)
    canonical_body_hash = canonical_body_fingerprint(canonical_body) if canonical_body else ""
    full_body_required = data.source_retention_mode == "full_body_required"
    transcript_archive = data.source_retention_mode == TRANSCRIPT_ARCHIVE_MODE
    archive_issues = (
        transcript_archive_issues(plan, data, written_notes=[source_note, application_note, *kp_notes])
        if transcript_archive else []
    )
    full_body_anchors_present = all(
        canonical_body_contains_anchor(canonical_body, anchor)
        for anchor in data.canonical_body_representative_anchors
    )
    coverage_audit_applicable = "## 覆蓋審計" in data.source_markdown or data.coverage_audit_status != "missing"
    verification_checks = {
        "source_note_exists": source_note.exists(),
        "frontmatter_present": text.startswith("---\n"),
        "source_input_kind_present": f"source_input_kind: {yaml_scalar(data.source_input_kind)}" in text,
        "source_identity_present": f"source_identity: {yaml_scalar(identity)}" in text,
        "source_retention_mode_present": f"source_retention_mode: {yaml_scalar(data.source_retention_mode)}" in text,
        "full_body_readback": (
            not full_body_required
            or (
                bool(canonical_body)
                and canonical_body_hash == data.canonical_body_sha256
                and bool(data.canonical_body_representative_anchors)
                and full_body_anchors_present
            )
        ),
        "full_body_registry_hash_matches": (
            not (full_body_required or transcript_archive)
            or registry_row.get("canonical_body_sha256") == data.canonical_body_sha256
        ),
        "transcript_archive_readback": (
            not transcript_archive
            or (
                not archive_issues
                and "## 字幕存檔" in text
                and not canonical_body
                and registry_row.get("source_retention_mode") == TRANSCRIPT_ARCHIVE_MODE
            )
        ),
        "registry_file_exists": registry_file.exists(),
        "registry_has_source_identity": identity in registry,
        "registry_points_to_source_note": registry_target == source_note,
        "registry_source_identity_matches": registry_source_identity(registry_row) == identity,
        "registry_source_url_matches": registry_row.get("source_url") == data.source_url if data.source_input_kind == "url" else "source_url" not in registry_row,
        "registry_source_content_hash_matches": registry_row.get("source_content_sha256") == data.source_content_sha256 if data.source_input_kind == "user_provided_text" else True,
        "registry_source_image_hash_matches": registry_row.get("source_image_sha256") == data.source_image_sha256 if data.source_input_kind == "user_provided_image" else True,
        "registry_source_id_matches": registry_row.get("source_id") == data.source_id,
        "knowledge_point_registry_file_exists": kp_registry_file.exists(),
        "knowledge_point_registry_has_kp_uids": all(uid in kp_registry for uid in expected_kp_uids),
        "knowledge_point_registry_paths_match": all(
            (vault / kp_registry.get(uid, {}).get("kp_note_path", "")) == path
            for uid, path in zip(expected_kp_uids, kp_notes)
        ),
        "knowledge_point_registry_semantic_keys_match": all(
            kp_registry.get(uid, {}).get("semantic_key") == knowledge_point_semantic_key(kp.text)
            for uid, kp in zip(expected_kp_uids, data.knowledge_points)
        ),
        "relation_registry_file_exists": relation_registry_file.exists(),
        "relation_registry_has_expected_relations": all(
            relation_id_value in relation_registry for relation_id_value in expected_relation_ids
        ),
        "relation_registry_expected_relations_are_linked": all(
            relation_registry.get(rid, {}).get("status", "linked") == "linked" for rid in expected_relation_ids
        ),
        "cross_source_relations_registered_and_linked": all(
            relation_registry.get(cross_source_link_id(data.source_id, link), {}).get("status") == "linked"
            for link in data.cross_source_links
        ),
        "source_relation_projection_matches_registry": {
            relation_id(data.source_id, r) for r in actual_source_relations
        } == set(expected_relation_ids),
        "knowledge_point_relation_projection_matches_registry": kp_relation_projection_matches,
        "source_note_uses_canonical_sharded_path": source_note == sharded_source_note(vault, data),
        "application_note_uses_canonical_sharded_path": application_note == sharded_application_note(vault, data),
        "knowledge_points_use_by_source_path": all(
            path.parent == vault / "knowledge-points" / "by-source" / data.source_id for path in kp_notes
        ),
        "asset_mode_present": f"asset_mode: {yaml_scalar(data.asset_mode)}" in text,
        "asset_embed_present": (
            data.asset_mode == ASSET_MODE_KNOWLEDGE
            or (
                "![[assets/" in text
                and (bool(asset_dests) if data.asset_mode == ASSET_MODE_FORMAL else bool(case_image_dests))
            )
        ),
        "knowledge_records_present": all(path.stem in text for path in kp_notes),
        "coverage_audit_readback": (
            data.asset_mode == ASSET_MODE_CASE
            and "case_evidence" in text
            and "## 覆蓋審計" in text
        ) or (
            data.asset_mode == ASSET_MODE_KNOWLEDGE
            and "## 覆蓋審計" in text
            and f"infographic_status: {yaml_scalar(data.infographic_status)}" in text
            and f"coverage_audit_status: {yaml_scalar(data.coverage_audit_status)}" in text
            and data.coverage_audit_status in {"complete", "text_only_pending_infographic"}
            and data.coverage_unit_total > 0
            and data.coverage_unit_infographic_mapped == 0
            and data.infographic_page_count == 0
            and data.infographic_multi_volume_status in {"not_requested", "not_generated"}
        ) or (
            not coverage_audit_applicable
            or (
                "## 覆蓋審計" in text
                and f"coverage_audit_status: {yaml_scalar(data.coverage_audit_status)}" in text
                and data.coverage_audit_status == "complete"
                and data.coverage_unit_total > 0
                and 0 < data.coverage_unit_infographic_mapped <= data.coverage_unit_total
                and data.infographic_page_count == len(asset_dests)
            )
        ),
        "record_layer_has_no_machine_wikilinks": not record_layer_machine_wikilink_issues_for_paths(
            [source_note, application_note, *kp_notes]
        ) if source_local else not record_layer_machine_wikilink_issues(vault),
        "asset_exists": all(
            isinstance(path, Path) and path.exists()
            for path in (asset_dests if data.asset_mode == ASSET_MODE_FORMAL else case_asset_dests)
        ),
        "case_evidence_manifest_readback": (
            data.asset_mode in {ASSET_MODE_FORMAL, ASSET_MODE_KNOWLEDGE}
            or (
                bool(case_asset_dests)
                and isinstance(case_asset_dests[0], Path)
                and case_asset_dests[0].exists()
                and hashlib.sha256(case_asset_dests[0].read_bytes()).hexdigest() == data.case_evidence_manifest_sha256
                and f"case_evidence_manifest_sha256: {yaml_scalar(data.case_evidence_manifest_sha256)}" in text
            )
        ),
        "case_evidence_screenshot_readback": (
            data.asset_mode in {ASSET_MODE_FORMAL, ASSET_MODE_KNOWLEDGE}
            or all(
                isinstance(path, Path) and path.exists() and f"![[{vault_relative(path)}]]" in text
                for path in case_image_dests
            )
        ),
        "source_original_image_readback": (
            data.source_input_kind != "user_provided_image"
            or (
                isinstance(source_image_dest, Path)
                and source_image_dest.exists()
                and hashlib.sha256(source_image_dest.read_bytes()).hexdigest() == data.source_image_sha256
                and f"![[{vault_relative(source_image_dest)}]]" in text
            )
        ),
        "knowledge_point_notes_exist": all(path.exists() for path in kp_notes),
        "knowledge_point_backlinks": all(
            source_note.stem in path.read_text(encoding="utf-8-sig") for path in kp_notes
        ),
        "knowledge_point_notes_have_semantic_keys": all(
            f"semantic_key: {yaml_scalar(knowledge_point_semantic_key(kp.text))}"
            in path.read_text(encoding="utf-8-sig")
            for kp, path in zip(data.knowledge_points, kp_notes)
        ),
        "knowledge_points_have_no_personal_properties": all(
            not any(
                marker in path.read_text(encoding="utf-8-sig")
                for marker in ("personal_relevance:", "taste_values:")
            )
            for path in kp_notes
        ),
        # A source-local ingest intentionally does not rewrite unrelated vault
        # navigation/base/concept material.  Those are whole-vault maintenance
        # checks, not evidence that this source's atomic writer/readback failed.
        "base_files_exist": source_local or all(path.exists() for path in base_files),
        "base_files_have_filters_and_views": source_local or all(
            "filters:" in path.read_text(encoding="utf-8-sig")
            and "views:" in path.read_text(encoding="utf-8-sig")
            for path in base_files
        ),
        "concept_notes_exist": source_local or all(path.exists() for path in expected_concept_notes),
        "concept_notes_reference_knowledge_points": source_local or all(
            kp_path.stem in concept_path.read_text(encoding="utf-8-sig")
            for kp_path, concept_path in expected_concept_pairs
            if concept_path is not None and concept_path.exists()
        ),
        "concept_notes_are_graph_clean": source_local or all(
            not re.search(r"\[\[(?:KP-|KS-|APP-)", concept_path.read_text(encoding="utf-8-sig"))
            for concept_path in expected_concept_notes
            if concept_path.exists()
        ),
        "no_notion_mainline_residue": not any(
            marker in source_note_without_canonical_section(text)
            for marker in NOTION_MAINLINE_RESIDUE
        ),
        "infographic_embed_count_matches": source_note_without_canonical_section(text).count("![[assets/") == (
            (len(asset_dests) if data.asset_mode == ASSET_MODE_FORMAL else len(case_image_dests))
            + (1 if data.source_input_kind == "user_provided_image" else 0)
        ),
        "infographic_block_validation_complete": data.asset_mode in {ASSET_MODE_CASE, ASSET_MODE_KNOWLEDGE} or (
            len(extract_infographic_blocks(text)) <= 1
            or not infographic_block_quality_issues_from_blocks(extract_infographic_blocks(text))
        ),
        "infographic_duplicate_content_clear": data.asset_mode in {ASSET_MODE_CASE, ASSET_MODE_KNOWLEDGE} or not infographic_duplicate_issues_from_blocks(extract_infographic_blocks(text)),
        "learning_check_complete": not learning_check_gaps(text),
        "source_note_has_no_adam_application_section": not has_personal_layer(
            source_note_without_canonical_section(text)
        ),
        "source_note_has_no_personal_properties": not any(
            marker in source_note_without_canonical_section(text)
            for marker in (
                "personal_relevance:",
                "action_status:",
                "life_scenes:",
                "taste_signals:",
                "anti_algorithm_status:",
                "exploration_intent:",
                "taste_network_status:",
            )
        ),
        "application_note_exists": application_note.exists(),
        "application_note_links_source": source_note.stem in application_text,
        "application_note_has_no_internal_action_sections": (
            "### 下一步" not in application_text and "### 仍需驗證" not in application_text
            and "可行動下一步" not in application_text
            and "仍需驗證" not in application_text
        ),
        "application_note_has_no_reader_residue": not application_reader_residue_terms(application_text),
        "application_note_has_no_notion_mainline_residue": not any(
            marker in application_text for marker in NOTION_MAINLINE_RESIDUE
        ),
        "source_note_has_no_excalidraw_embed": ".excalidraw" not in source_note_without_canonical_section(text) and "Visual Map" not in source_note_without_canonical_section(text),
        "application_note_has_no_excalidraw_embed": ".excalidraw" not in application_text and "Working Map" not in application_text,
        "reader_layer_order": (
            text.find("## 資訊圖") != -1
            and text.find("## 學習檢查") > text.find("## 資訊圖")
            if data.asset_mode == ASSET_MODE_FORMAL
            else text.find("## 學習檢查") != -1
            if data.asset_mode == ASSET_MODE_KNOWLEDGE
            else text.find("## 案例證據") != -1 and text.find("## 學習檢查") > text.find("## 案例證據")
        ),
    }
    verification = {
        **verification_checks,
        "application_note_has_adoption_properties": (
            "adoption_status:" in application_text and "adoption_reason:" in application_text
        ),
        "application_note_has_adoption_section": "## AI 採納判斷" in application_text,
        "application_note_has_ai_adoption_properties": (
            "ai_adoption_status:" in application_text
            and "ai_adoption_reason:" in application_text
        ),
        "application_note_has_ai_adoption_section": "## AI 採納判斷" in application_text,
        "application_note_has_confirmation_properties": (
            "manual_correction_status:" in application_text
            and "manual_correction_action:" in application_text
            and "ai_review_confidence:" in application_text
        ),
        "application_note_has_review_properties": (
            "taste_network_reason:" in application_text
            and "human_verification_items:" in application_text
            and "personalization_status:" in application_text
            and "personalization_links:" in application_text
            and "taste_link_status:" in application_text
            and "taste_links:" in application_text
        ),
        "application_note_has_review_sections": (
            "## 品味網判斷" in application_text
            and "## 個人化扣連狀態" in application_text
            and "## 品味扣連狀態" in application_text
            and "## 仍需人工驗證事項" in application_text
        ),
        "application_note_has_compounding_properties": (
            "compounding_insight_status:" in application_text
            and "compounding_insight_types:" in application_text
            and "compounding_insight_links:" in application_text
            and "compounding_next_action:" in application_text
        ),
        "application_note_has_compounding_section": "## 跨來源洞見判斷" in application_text,
        "full_body_evidence": {
            "mode": data.source_retention_mode,
            "canonical_body_sha256": data.canonical_body_sha256 if full_body_required else "",
            "readback_sha256": canonical_body_hash if full_body_required else "",
            "representative_anchors": list(data.canonical_body_representative_anchors) if full_body_required else [],
            "representative_anchors_present": full_body_anchors_present if full_body_required else True,
        },
        "transcript_archive_evidence": {
            "mode": data.source_retention_mode,
            "verified": transcript_archive and not archive_issues,
            "issues": [issue["code"] for issue in archive_issues],
        },
        "enum_violations": enum_violations,
        "adoption_field_gaps": adoption_field_gaps,
        "application_review_field_gaps": review_field_gaps,
        "application_compounding_field_gaps": compounding_field_gaps,
    }
    verification["passed"] = all(bool(value) for value in verification_checks.values()) and not compounding_field_gaps
    return verification


def vault_health_report(root: Path, vault: Path) -> dict[str, object]:
    registry_file = source_registry_file(vault)
    kp_registry_file = knowledge_point_registry_file(vault)
    rel_registry_file = relation_registry_file(vault)
    rows = read_source_registry_rows(vault)
    row_items = [row for _, row in rows]
    kp_registry_rows = [row for _, row in read_jsonl_rows(kp_registry_file)]
    rel_registry_rows = [row for _, row in read_jsonl_rows(rel_registry_file)]
    source_identities = [registry_source_identity(row) for row in row_items]
    source_hashes = [row.get("source_url_hash", "") for row in row_items if row.get("source_input_kind", "url") == "url"]
    source_urls = [canonical_url(row.get("source_url", "")) for row in row_items if row.get("source_input_kind", "url") == "url"]
    source_ids = [row.get("source_id", "") for row in row_items]
    registry_targets = [vault / row.get("source_note_path", "") for row in row_items]
    kp_uids = [row.get("kp_uid", "") for row in kp_registry_rows]
    relation_ids = [row.get("relation_id", "") for row in rel_registry_rows]
    kp_registry_targets = [vault / row.get("kp_note_path", "") for row in kp_registry_rows]

    source_files = sorted((vault / "sources").rglob("*.md")) if (vault / "sources").exists() else []
    application_files = sorted((vault / "applications").rglob("*.md")) if (vault / "applications").exists() else []
    kp_files = sorted((vault / "knowledge-points").rglob("*.md")) if (vault / "knowledge-points").exists() else []
    concept_files = sorted((vault / "concepts").rglob("*.md")) if (vault / "concepts").exists() else []

    registry_target_set = {path.resolve() for path in registry_targets}
    source_file_set = {path.resolve() for path in source_files}
    kp_registry_target_set = {path.resolve() for path in kp_registry_targets}
    kp_file_set = {path.resolve() for path in kp_files}

    flat_sources = [path for path in source_files if is_flat_source_file(vault, path)]
    flat_applications = [path for path in application_files if is_flat_application_file(vault, path)]
    flat_kps = [path for path in kp_files if is_flat_knowledge_point_file(vault, path)]
    noncanonical_sources = [path for path in source_files if not is_canonical_source_file(vault, path)]
    noncanonical_applications = [path for path in application_files if not is_canonical_application_file(vault, path)]
    noncanonical_kps = [path for path in kp_files if not is_canonical_kp_file(vault, path)]

    missing_registry_targets = [path for path in registry_targets if not path.exists()]
    missing_kp_registry_targets = [path for path in kp_registry_targets if not path.exists()]
    unregistered_sources = [path for path in source_files if path.resolve() not in registry_target_set]
    unregistered_kps = [path for path in kp_files if path.resolve() not in kp_registry_target_set]
    registry_targets_not_sources = [path for path in registry_targets if path.resolve() not in source_file_set]
    kp_registry_targets_not_kps = [path for path in kp_registry_targets if path.resolve() not in kp_file_set]
    source_content_issues = source_note_content_issues(vault, row_items)
    kp_content_issues = knowledge_point_content_issues(vault, kp_registry_rows)
    relation_registry_issues = relation_content_issues(kp_registry_rows, rel_registry_rows)
    enum_violations = source_note_enum_violations(vault, source_files)
    source_validation_gaps = source_ai_validation_field_gaps(vault, source_files)
    source_infographic_asset_gaps = source_infographic_asset_gaps_report(vault, source_files)
    learning_check_issues = source_learning_check_issues(vault, source_files)
    infographic_coverage_gaps = source_infographic_coverage_gaps(vault, source_files)
    infographic_block_quality_issues = source_infographic_block_quality_issues(vault, source_files)
    infographic_duplicate_content_issues = source_infographic_duplicate_content_issues(vault, source_files)
    adoption_field_gaps = application_adoption_field_gaps(vault, application_files)
    review_field_gaps = application_review_field_gaps(vault, application_files)
    compounding_field_gaps = application_compounding_field_gaps(vault, application_files)
    machine_wikilink_issues = record_layer_machine_wikilink_issues(vault)
    expected_concept_files = []
    concept_content_issues = []
    for kp_file in kp_files:
        fields = frontmatter_fields(kp_file)
        knowledge_point = str(fields.get("knowledge_point") or fields.get("definition") or kp_file.stem)
        concept_path = projected_concept_note_path(vault, fields, knowledge_point)
        if concept_path is None:
            continue
        expected_concept_files.append(concept_path)
        if not concept_path.exists():
            concept_content_issues.append(
                {
                    "path": str(concept_path.relative_to(vault)),
                    "issue": "missing_concept_note",
                    "kp_note": str(kp_file.relative_to(vault)),
                }
            )
            continue
        concept_text = concept_path.read_text(encoding="utf-8-sig")
        concept_fields = frontmatter_fields(concept_path)
        concept_name = str(concept_fields.get("concept") or concept_path.stem)
        concept_title_issue = concept_title_quality_issue(concept_name)
        if concept_title_issue:
            concept_content_issues.append(
                {
                    "path": str(concept_path.relative_to(vault)),
                    "issue": concept_title_issue,
                    "kp_note": str(kp_file.relative_to(vault)),
                }
            )
        if "type: \"concept\"" not in concept_text or kp_file.stem not in concept_text:
            concept_content_issues.append(
                {
                    "path": str(concept_path.relative_to(vault)),
                    "issue": "concept_note_missing_type_or_kp_record",
                    "kp_note": str(kp_file.relative_to(vault)),
                }
            )
        if re.search(r"\[\[(?:KP-|KS-|APP-)", concept_text):
            concept_content_issues.append(
                {
                    "path": str(concept_path.relative_to(vault)),
                    "issue": "concept_note_has_machine_id_wikilink",
                    "kp_note": str(kp_file.relative_to(vault)),
                }
            )
    kp_source_folder_issues = [
        path for path in kp_files
        if is_canonical_kp_file(vault, path) and path.relative_to(vault).parts[2] not in source_ids
    ]

    checks = {
        "registry_file_exists": registry_file.exists(),
        "registry_has_rows": bool(row_items),
        "source_identities_unique": unique_non_empty(source_identities),
        "source_url_hashes_unique": unique_non_empty(source_hashes),
        "source_urls_unique": unique_non_empty(source_urls),
        "source_ids_unique": unique_non_empty(source_ids),
        "registry_targets_exist": not missing_registry_targets,
        "registry_targets_are_source_files": not registry_targets_not_sources,
        "knowledge_point_registry_file_exists": kp_registry_file.exists(),
        "knowledge_point_registry_has_rows": bool(kp_registry_rows),
        "knowledge_point_uids_unique": unique_non_empty(kp_uids),
        "knowledge_point_registry_targets_exist": not missing_kp_registry_targets,
        "knowledge_point_registry_targets_are_kp_files": not kp_registry_targets_not_kps,
        "no_unregistered_knowledge_point_notes": not unregistered_kps,
        "relation_registry_file_exists": rel_registry_file.exists(),
        "relation_ids_unique": unique_non_empty(relation_ids) if rel_registry_rows else True,
        "relation_registry_targets_known_kps": not relation_registry_issues,
        "no_unregistered_source_notes": not unregistered_sources,
        "no_flat_source_notes": not flat_sources,
        "no_flat_application_notes": not flat_applications,
        "no_flat_knowledge_points": not flat_kps,
        "all_source_notes_canonical": not noncanonical_sources,
        "all_application_notes_canonical": not noncanonical_applications,
        "all_knowledge_points_canonical": not noncanonical_kps,
        "knowledge_point_folders_match_registry_sources": not kp_source_folder_issues,
        "source_note_content_matches_registry": not source_content_issues,
        "knowledge_point_content_matches_registry": not kp_content_issues,
        "source_notes_have_ai_validation_fields": not source_validation_gaps,
        "source_infographic_assets_are_real_png": not source_infographic_asset_gaps,
        "source_notes_have_learning_tasks": not learning_check_issues,
        "source_notes_have_infographic_coverage_fields": not infographic_coverage_gaps,
        "source_infographic_blocks_are_validated": not infographic_block_quality_issues,
        "source_infographics_have_distinct_content": not infographic_duplicate_content_issues,
        "application_notes_have_adoption_fields": not adoption_field_gaps,
        "application_notes_have_review_fields": not review_field_gaps,
        "application_notes_have_compounding_fields": not compounding_field_gaps,
        "concept_notes_cover_knowledge_points": not concept_content_issues,
        "record_layer_has_no_machine_wikilinks": not machine_wikilink_issues,
    }
    issues = {
        "missing_registry_targets": relative_paths(root, missing_registry_targets),
        "missing_knowledge_point_registry_targets": relative_paths(root, missing_kp_registry_targets),
        "unregistered_sources": relative_paths(root, unregistered_sources),
        "unregistered_knowledge_points": relative_paths(root, unregistered_kps),
        "registry_targets_not_sources": relative_paths(root, registry_targets_not_sources),
        "knowledge_point_registry_targets_not_kps": relative_paths(root, kp_registry_targets_not_kps),
        "flat_sources": relative_paths(root, flat_sources),
        "flat_applications": relative_paths(root, flat_applications),
        "flat_knowledge_points": relative_paths(root, flat_kps),
        "noncanonical_sources": relative_paths(root, noncanonical_sources),
        "noncanonical_applications": relative_paths(root, noncanonical_applications),
        "noncanonical_knowledge_points": relative_paths(root, noncanonical_kps),
        "knowledge_point_source_folder_issues": relative_paths(root, kp_source_folder_issues),
        "source_content_issues": source_content_issues,
        "knowledge_point_content_issues": kp_content_issues,
        "relation_registry_issues": relation_registry_issues,
        "enum_violations": enum_violations,
        "source_ai_validation_field_gaps": source_validation_gaps,
        "source_infographic_asset_gaps": source_infographic_asset_gaps,
        "source_learning_check_issues": learning_check_issues,
        "source_infographic_coverage_gaps": infographic_coverage_gaps,
        "source_infographic_block_quality_issues": infographic_block_quality_issues,
        "source_infographic_duplicate_content_issues": infographic_duplicate_content_issues,
        "application_adoption_field_gaps": adoption_field_gaps,
        "application_review_field_gaps": review_field_gaps,
        "application_compounding_field_gaps": compounding_field_gaps,
        "concept_content_issues": concept_content_issues,
        "record_layer_machine_wikilink_issues": machine_wikilink_issues,
    }
    assessment = classify_vault_health(checks, issues)
    return {
        "mode": "vault_health",
        "status": assessment["status"],
        "assessment": assessment,
        "registry": str(registry_file.relative_to(root)),
        "counts": {
            "registry_rows": len(row_items),
            "knowledge_point_registry_rows": len(kp_registry_rows),
            "relation_registry_rows": len(rel_registry_rows),
            "source_notes": len(source_files),
            "application_notes": len(application_files),
            "knowledge_point_notes": len(kp_files),
            "concept_notes": len(concept_files),
        },
        "checks": checks,
        "issues": issues,
        "enum_violations": enum_violations,
        "source_ai_validation_field_gaps": source_validation_gaps,
        "source_infographic_asset_gaps": source_infographic_asset_gaps,
        "source_learning_check_issues": learning_check_issues,
        "source_infographic_coverage_gaps": infographic_coverage_gaps,
        "source_infographic_block_quality_issues": infographic_block_quality_issues,
        "source_infographic_duplicate_content_issues": infographic_duplicate_content_issues,
        "application_adoption_field_gaps": adoption_field_gaps,
        "application_review_field_gaps": review_field_gaps,
        "application_compounding_field_gaps": compounding_field_gaps,
        "concept_content_issues": concept_content_issues,
        "record_layer_machine_wikilink_issues": machine_wikilink_issues,
    }


def classify_vault_health(checks: dict[str, bool], issues: dict[str, list]) -> dict[str, object]:
    """Separate source integrity, unverified evidence and optional graph upkeep.

    Raw checks/issues remain available. Missing review evidence is never passed
    by age, and a missing concept does not invalidate source-local ingestion.
    """
    evidence_checks = {
        "source_notes_have_ai_validation_fields",
        "source_infographic_blocks_are_validated",
    }
    evidence_keys = (
        "source_ai_validation_field_gaps", "source_infographic_block_quality_issues",
    )
    concept_issues = issues.get("concept_content_issues", [])
    maintenance = [item for item in concept_issues if item.get("issue") == "missing_concept_note"]
    concept_defects = [item for item in concept_issues if item.get("issue") != "missing_concept_note"]
    integrity_failures = [
        key for key, passed in checks.items()
        if not passed and key not in evidence_checks | {"concept_notes_cover_knowledge_points"}
    ]
    if concept_defects:
        integrity_failures.append("existing_concept_content_invalid")
    # Keep malformed metadata blocking even if it is not a legacy flat check.
    if issues.get("enum_violations"):
        integrity_failures.append("source_note_enum_violations")
    evidence = [dict(item, category=key) for key in evidence_keys for item in issues.get(key, [])]
    evidence_failures = sorted(key for key in evidence_checks if checks.get(key) is False)
    needs_review = bool(integrity_failures or evidence or evidence_failures)
    return {
        "status": "needs_review" if needs_review else "passed",
        "integrity": {
            "status": "needs_review" if integrity_failures else "passed",
            "failed_checks": integrity_failures,
            "concept_defects": concept_defects,
        },
        "evidence_review": {
            "status": "needs_review" if evidence or evidence_failures else "passed",
            "failed_checks": evidence_failures,
            "affected_source_count": len({item["path"] for item in evidence}),
            "issues": evidence,
            "interpretation": "Evidence does not satisfy current checks; historical acceptance is not revoked or upgraded automatically. Inspect original receipts before deciding whether evidence is missing or the check is incompatible.",
        },
        "maintenance": {
            "status": "pending" if maintenance else "ready",
            "blocking": False,
            "items": maintenance,
            "interpretation": "Review concept candidates through semantic maintenance; source-local completion does not require generating a concept for every knowledge point.",
        },
    }


def source_note_content_issues(vault: Path, rows: list[dict[str, str]]) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []
    for row in rows:
        path = vault / row.get("source_note_path", "")
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8-sig")
        expected = {"source_id": row.get("source_id", "")}
        explicit_input_kind = row.get("source_input_kind", "")
        if explicit_input_kind:
            expected["source_input_kind"] = explicit_input_kind
            expected["source_identity"] = registry_source_identity(row)
        retention_mode = row.get("source_retention_mode", "")
        if retention_mode:
            expected["source_retention_mode"] = retention_mode
        if not explicit_input_kind or explicit_input_kind == "url":
            expected["source_url"] = row.get("source_url", "")
            expected["source_url_hash"] = row.get("source_url_hash", "")
        elif explicit_input_kind == "user_provided_text":
            expected["source_content_sha256"] = row.get("source_content_sha256", "")
        elif explicit_input_kind == "user_provided_image":
            expected["source_image_sha256"] = row.get("source_image_sha256", "")
        if retention_mode in CANONICAL_HASH_RETENTION_MODES:
            expected["canonical_body_sha256"] = row.get("canonical_body_sha256", "")
        for field, value in expected.items():
            if f"{field}: {yaml_scalar(value)}" not in text:
                issues.append({"path": vault_relative(path), "issue": f"{field}_mismatch"})
        if retention_mode == "full_body_required":
            canonical_body = extract_canonical_body_markdown(text)
            if not canonical_body:
                issues.append({"path": vault_relative(path), "issue": "full_body_missing"})
            elif canonical_body_fingerprint(canonical_body) != row.get("canonical_body_sha256", ""):
                issues.append({"path": vault_relative(path), "issue": "full_body_hash_mismatch"})
        if retention_mode == TRANSCRIPT_ARCHIVE_MODE:
            if canonical_body_markdown_present(text):
                issues.append({"path": vault_relative(path), "issue": "transcript_body_in_vault"})
            if has_cue_timing_line(text):
                issues.append({"path": vault_relative(path), "issue": "transcript_cue_timing_line_in_vault"})
        if any(marker in source_note_without_canonical_section(text) for marker in SOURCE_INTERNAL_RESIDUE):
            issues.append({"path": vault_relative(path), "issue": "source_internal_residue"})
        if has_personal_layer(text):
            issues.append({"path": vault_relative(path), "issue": "adam_application_residue"})
    return issues


def source_note_enum_violations(vault: Path, source_files: list[Path]) -> list[dict[str, str]]:
    violations: list[dict[str, str]] = []
    for path in source_files:
        fields = frontmatter_fields(path)
        source_input_kind = fields.get("source_input_kind", "url")
        if isinstance(source_input_kind, str) and source_input_kind not in VALID_SOURCE_INPUT_KINDS:
            violations.append({
                "path": vault_relative(path),
                **enum_violation("source_input_kind", source_input_kind, VALID_SOURCE_INPUT_KINDS),
            })
        source_retention_mode = fields.get("source_retention_mode", "summarized_allowed")
        if isinstance(source_retention_mode, str) and source_retention_mode not in VALID_SOURCE_RETENTION_MODES:
            violations.append({
                "path": vault_relative(path),
                **enum_violation("source_retention_mode", source_retention_mode, VALID_SOURCE_RETENTION_MODES),
            })
        source_type = fields.get("source_type", "")
        if isinstance(source_type, str) and source_type and source_type not in VALID_SOURCE_TYPES:
            violations.append({
                "path": vault_relative(path),
                **enum_violation("source_type", source_type, VALID_SOURCE_TYPES),
            })
        knowledge_use = fields.get("knowledge_use", [])
        if isinstance(knowledge_use, str):
            knowledge_use = [knowledge_use]
        for value in knowledge_use:
            if value not in VALID_KNOWLEDGE_USE:
                violations.append({
                    "path": vault_relative(path),
                    **enum_violation("knowledge_use", value, VALID_KNOWLEDGE_USE),
                })
    return violations


def source_ai_validation_field_gaps(vault: Path, source_files: list[Path]) -> list[dict[str, str]]:
    gaps: list[dict[str, str]] = []
    required = (
        "ai_source_analysis_status",
        "ai_source_analysis_evidence",
        "png_generation_method",
        "png_semantic_validation_status",
        "png_semantic_validation_evidence",
    )
    for path in source_files:
        fields = frontmatter_fields(path)
        missing = [field for field in required if field not in fields]
        invalid = []
        if fields.get("ai_source_analysis_status") not in {"complete", "completed"}:
            invalid.append("ai_source_analysis_status")
        generation_method = str(fields.get("png_generation_method") or "")
        if png_generation_method_issue(generation_method):
            invalid.append("png_generation_method")
        if fields.get("png_semantic_validation_status") not in {"passed", "pass"}:
            invalid.append("png_semantic_validation_status")
        if missing or invalid:
            gaps.append({
                "path": vault_relative(path),
                "missing_fields": ", ".join(missing),
                "invalid_fields": ", ".join(invalid),
            })
    return gaps


def source_infographic_asset_gaps_report(vault: Path, source_files: list[Path]) -> list[dict[str, str]]:
    gaps: list[dict[str, str]] = []
    vault_root = vault.resolve()
    for path in source_files:
        fields = frontmatter_fields(path)
        raw_assets = fields.get("infographics")
        if isinstance(raw_assets, list):
            assets = [str(item) for item in raw_assets if str(item).strip()]
        elif raw_assets:
            assets = [str(raw_assets)]
        else:
            assets = []
        if not assets:
            gaps.append({
                "path": vault_relative(path),
                "issue": "missing_infographics_field",
            })
            continue
        for asset in assets:
            asset_path = Path(asset)
            if asset_path.is_absolute():
                gaps.append({
                    "path": vault_relative(path),
                    "asset": asset,
                    "issue": "absolute_asset_path",
                })
                continue
            target = (vault / asset_path).resolve()
            if target != vault_root and vault_root not in target.parents:
                gaps.append({
                    "path": vault_relative(path),
                    "asset": asset,
                    "issue": "asset_outside_vault",
                })
            elif not target.exists():
                gaps.append({
                    "path": vault_relative(path),
                    "asset": asset,
                    "issue": "missing_asset_file",
                })
            elif png_file_issue(target) is not None:
                gaps.append({
                    "path": vault_relative(path),
                    "asset": asset,
                    "issue": png_file_issue(target) or "invalid_png_asset",
                })
    return gaps


def source_learning_check_issues(vault: Path, source_files: list[Path]) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []
    for path in source_files:
        text = path.read_text(encoding="utf-8-sig")
        missing = learning_check_gaps(text)
        if missing:
            issues.append({
                "path": vault_relative(path),
                "missing_types": ", ".join(missing),
            })
    return issues


def source_infographic_coverage_gaps(vault: Path, source_files: list[Path]) -> list[dict[str, str]]:
    gaps: list[dict[str, str]] = []
    required_body_labels = ("重要性覆蓋", "本 URL 資訊圖數量判斷")
    required_frontmatter = ("infographic_importance_coverage", "infographic_count_decision")
    for path in source_files:
        text = path.read_text(encoding="utf-8-sig")
        fields = frontmatter_fields_from_text(text)
        missing_body = [label for label in required_body_labels if f"- {label}:" not in text and f"- {label}：" not in text]
        missing_frontmatter = [
            field
            for field in required_frontmatter
            if incomplete_free_text(str(fields.get(field) or ""))
        ]
        if missing_body or missing_frontmatter:
            gaps.append({
                "path": vault_relative(path),
                "missing_body_labels": ", ".join(missing_body),
                "missing_frontmatter": ", ".join(missing_frontmatter),
            })
    return gaps


def source_infographic_duplicate_content_issues(vault: Path, source_files: list[Path]) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []
    for path in source_files:
        text = path.read_text(encoding="utf-8-sig")
        duplicate_issues = infographic_duplicate_issues_from_blocks(extract_infographic_blocks(text))
        for issue in duplicate_issues:
            issues.append({"path": vault_relative(path), **issue})
    return issues


def source_infographic_block_quality_issues(vault: Path, source_files: list[Path]) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []
    for path in source_files:
        text = path.read_text(encoding="utf-8-sig")
        blocks = extract_infographic_blocks(text)
        if len(blocks) <= 1:
            continue
        block_issues = infographic_block_quality_issues_from_blocks(blocks)
        for issue in block_issues:
            issues.append({"path": vault_relative(path), **issue})
    return issues


def application_adoption_field_gaps(vault: Path, application_files: list[Path]) -> list[dict[str, str]]:
    gaps: list[dict[str, str]] = []
    required = (
        "adoption_status",
        "adoption_reason",
        "ai_adoption_status",
        "ai_adoption_reason",
        "ai_review_confidence",
        "manual_correction_status",
        "manual_correction_action",
    )
    for path in application_files:
        text = path.read_text(encoding="utf-8-sig")
        fields = frontmatter_fields_from_text(text)
        missing = [field for field in required if field not in fields]
        missing_sections = []
        if "## AI 採納判斷" not in text:
            missing_sections.append("AI 採納判斷")
        if "## 人工修正" not in text:
            missing_sections.append("人工修正")
        if missing or missing_sections:
            gaps.append({
                "path": vault_relative(path),
                "missing_fields": ", ".join(missing),
                "missing_sections": ", ".join(missing_sections),
            })
    return gaps


def application_review_field_gaps(vault: Path, application_files: list[Path]) -> list[dict[str, str]]:
    gaps: list[dict[str, str]] = []
    required = (
        "taste_network_reason",
        "human_verification_items",
        "personalization_status",
        "personalization_links",
        "taste_link_status",
        "taste_links",
        "ingest_status",
        "next_action",
        "issue_reason",
        "png_entry",
        "source_entry",
        "application_entry",
        "knowledge_points_entry",
    )
    for path in application_files:
        text = path.read_text(encoding="utf-8-sig")
        fields = frontmatter_fields_from_text(text)
        missing = [field for field in required if field not in fields]
        missing_sections = []
        if "## 品味網判斷" not in text:
            missing_sections.append("品味網判斷")
        if "## 個人化扣連狀態" not in text:
            missing_sections.append("個人化扣連狀態")
        if "## 品味扣連狀態" not in text:
            missing_sections.append("品味扣連狀態")
        if "## 仍需人工驗證事項" not in text:
            missing_sections.append("仍需人工驗證事項")
        if missing or missing_sections:
            gaps.append({
                "path": vault_relative(path),
                "missing_fields": ", ".join(missing),
                "missing_sections": ", ".join(missing_sections),
            })
    return gaps


def application_compounding_field_gaps(vault: Path, application_files: list[Path]) -> list[dict[str, str]]:
    gaps: list[dict[str, str]] = []
    required = (
        "compounding_insight_status",
        "compounding_insight_types",
        "compounding_insight_links",
        "compounding_next_action",
    )
    for path in application_files:
        text = path.read_text(encoding="utf-8-sig")
        fields = frontmatter_fields_from_text(text)
        missing = [field for field in required if field not in fields]
        invalid: list[str] = []
        status = frontmatter_scalar(text, "compounding_insight_status")
        if status and status not in VALID_COMPOUNDING_STATUSES:
            invalid.append("compounding_insight_status")
        section = text if "## 跨來源洞見判斷" in text else ""
        missing_sections = []
        if "## 跨來源洞見判斷" not in text:
            missing_sections.append("跨來源洞見判斷")
        missing_categories = [
            label for label in VALID_COMPOUNDING_TYPES
            if f"{label}:" not in section and f"{label}：" not in section
        ]
        if missing_categories:
            invalid.append("missing category judgments " + "/".join(missing_categories))
        residue_terms = application_reader_residue_terms(text)
        if residue_terms:
            invalid.append("reader-facing residue " + "/".join(residue_terms))
        if status == "has_insight":
            links = frontmatter_list_items(text, "compounding_insight_links")
            types = frontmatter_list_items(text, "compounding_insight_types")
            next_action = frontmatter_scalar(text, "compounding_next_action")
            if not links:
                invalid.append("has_insight missing links")
            if not types:
                invalid.append("has_insight missing types")
            if not next_action or next_action == "暫無":
                invalid.append("has_insight missing next action")
        if missing or invalid or missing_sections:
            gaps.append({
                "path": vault_relative(path),
                "missing_fields": ", ".join(missing),
                "invalid_fields": ", ".join(invalid),
                "missing_sections": ", ".join(missing_sections),
            })
    return gaps


def record_layer_machine_wikilink_issues(vault: Path) -> list[dict[str, str]]:
    paths: list[Path] = []
    for folder in ("sources", "applications", "knowledge-points"):
        root = vault / folder
        if not root.exists():
            continue
        paths.extend(sorted(root.rglob("*.md")))
    return record_layer_machine_wikilink_issues_for_paths(paths)


def record_layer_machine_wikilink_issues_for_paths(paths: list[Path]) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []
    for path in paths:
        text = path.read_text(encoding="utf-8-sig")
        matches = sorted({match.group(1) for match in MACHINE_ID_WIKILINK_RE.finditer(text)})
        if matches:
            issues.append(
                {
                    "path": vault_relative(path),
                    "machine_wikilinks": ", ".join(matches),
                }
            )
    return issues


def frontmatter_fields(path: Path) -> dict[str, str | list[str]]:
    text = path.read_text(encoding="utf-8-sig")
    return frontmatter_fields_from_text(text)


def frontmatter_fields_from_text(text: str) -> dict[str, str | list[str]]:
    if not text.startswith("---\n"):
        return {}
    end = text.find("\n---", 4)
    if end == -1:
        return {}
    lines = text[4:end].splitlines()
    fields: dict[str, str | list[str]] = {}
    index = 0
    while index < len(lines):
        line = lines[index]
        if not line.strip() or line.startswith(" "):
            index += 1
            continue
        if ":" not in line:
            index += 1
            continue
        key, raw_value = line.split(":", 1)
        key = key.strip()
        value = raw_value.strip()
        if value == "[]":  # render_frontmatter writes an empty list this way
            fields[key] = []
            index += 1
            continue
        if value:
            fields[key] = unquote_yaml_scalar(value)
            index += 1
            continue
        items: list[str] = []
        index += 1
        while index < len(lines) and lines[index].startswith("  - "):
            items.append(unquote_yaml_scalar(lines[index][4:].strip()))
            index += 1
        fields[key] = items
    return fields


def frontmatter_scalar(text: str, key: str) -> str:
    value = frontmatter_fields_from_text(text).get(key)
    return value if isinstance(value, str) else ""


def frontmatter_list_items(text: str, key: str) -> list[str]:
    value = frontmatter_fields_from_text(text).get(key)
    return value if isinstance(value, list) else []


def fields_list(fields: dict, key: str) -> list[str]:
    value = fields.get(key)
    return value if isinstance(value, list) else []


def unquote_yaml_scalar(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value.startswith('"') and value.endswith('"'):
        return value[1:-1].replace('\\"', '"').replace("\\\\", "\\")
    return value


def knowledge_point_content_issues(vault: Path, rows: list[dict[str, str]]) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []
    for row in rows:
        path = vault / row.get("kp_note_path", "")
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8-sig")
        expected = {
            "kp_uid": row.get("kp_uid", ""),
            "kp_id": row.get("kp_id", ""),
            "semantic_key": row.get("semantic_key", ""),
        }
        for field, value in expected.items():
            if f"{field}: {yaml_scalar(value)}" not in text:
                issues.append({"path": vault_relative(path), "issue": f"{field}_mismatch"})
    return issues


def relation_content_issues(
    kp_rows: list[dict[str, str]],
    relation_rows: list[dict[str, str]],
) -> list[dict[str, str]]:
    known_uids = {row.get("kp_uid", "") for row in kp_rows}
    issues: list[dict[str, str]] = []
    for row in relation_rows:
        relation_key = row.get("relation_id", "")
        source_uid = row.get("source_kp_uid", "")
        target_uid = row.get("target_kp_uid", "")
        if source_uid not in known_uids:
            issues.append({"relation_id": relation_key, "issue": "source_kp_uid_missing"})
        if target_uid not in known_uids:
            issues.append({"relation_id": relation_key, "issue": "target_kp_uid_missing"})
    return issues


def is_flat_source_file(vault: Path, path: Path) -> bool:
    relative = path.relative_to(vault)
    return len(relative.parts) == 2 and relative.parts[0] == "sources"


def is_flat_application_file(vault: Path, path: Path) -> bool:
    relative = path.relative_to(vault)
    return len(relative.parts) == 2 and relative.parts[0] == "applications"


def is_flat_knowledge_point_file(vault: Path, path: Path) -> bool:
    relative = path.relative_to(vault)
    return len(relative.parts) == 2 and relative.parts[0] == "knowledge-points"


def is_canonical_source_file(vault: Path, path: Path) -> bool:
    relative = path.relative_to(vault)
    parts = relative.parts
    return (
        len(parts) == 4
        and parts[0] == "sources"
        and is_year(parts[1])
        and is_month(parts[2])
        and path.suffix == ".md"
    )


def is_canonical_application_file(vault: Path, path: Path) -> bool:
    relative = path.relative_to(vault)
    parts = relative.parts
    return (
        len(parts) == 4
        and parts[0] == "applications"
        and is_year(parts[1])
        and is_month(parts[2])
        and path.name.startswith("APP-")
        and path.suffix == ".md"
    )


def is_canonical_kp_file(vault: Path, path: Path) -> bool:
    relative = path.relative_to(vault)
    parts = relative.parts
    return (
        len(parts) == 4
        and parts[0] == "knowledge-points"
        and parts[1] == "by-source"
        and parts[2].startswith("KS-")
        and path.suffix == ".md"
    )


def is_year(value: str) -> bool:
    return bool(re.fullmatch(r"\d{4}", value))


def is_month(value: str) -> bool:
    return bool(re.fullmatch(r"\d{2}", value))


def unique_non_empty(values: list[str]) -> bool:
    cleaned = [value for value in values if value]
    return len(cleaned) == len(values) and len(set(cleaned)) == len(cleaned)


def relative_paths(root: Path, paths: list[Path]) -> list[str]:
    return [str(path.relative_to(root)) for path in paths]


def print_human(result: dict[str, object]) -> None:
    print(f"mode: {result['mode']}")
    print(f"status: {result['status']}")
    if result.get("write_blocked") and "registry" not in result:
        print("issues:")
        for issue in result.get("issues", []):
            print(f"  - {issue}")
        return
    if result["mode"] == "vault_health":
        print(f"registry: {result['registry']}")
        print(f"counts: {result['counts']}")
        assessment = result["assessment"]
        print(f"integrity: {assessment['integrity']['status']}")
        print(f"evidence review: {assessment['evidence_review']['status']} ({assessment['evidence_review']['affected_source_count']} sources)")
        print(f"concept maintenance: {assessment['maintenance']['status']} ({len(assessment['maintenance']['items'])} items; non-blocking)")
        print("checks:")
        checks = result["checks"]
        assert isinstance(checks, dict)
        for key, value in checks.items():
            print(f"  {key}: {value}")
        return
    if result["mode"] == "duplicate_review":
        print(f"registry: {result['registry']}")
        print(f"candidate count: {result['candidate_count']}")
        for candidate in result["candidates"]:
            print(f"  - {candidate}")
        return
    if result["mode"] == "duplicate_resolve":
        print(f"registry: {result['registry']}")
        print(f"kp uid: {result['kp_uid']}")
        print(f"decision: {result['decision']}")
        print(f"duplicate of: {result['duplicate_of']}")
        print(f"kp note path: {result['kp_note_path']}")
        if result.get("decision_receipt"):
            print(f"decision receipt: {result['decision_receipt']}")
        for revision in result.get("completion_revisions", []):
            print(f"completion revision: {revision['source_id']} {revision['record']}")
        if "verification" in result:
            print(f"verification: {result['verification']}")
        return
    print(f"registry: {result['registry']}")
    print(f"source identity: {result['source_identity']}")
    print(f"registry resolution: {result['registry_resolution']}")
    if result["registry_issue"]:
        print(f"registry issue: {result['registry_issue']}")
    print(f"source note: {result['source_note']}")
    print(f"application note: {result['application_note']}")
    print("knowledge points:")
    for item in result["knowledge_points"]:
        print(f"  - {item}")
    print(f"asset: {result['asset']}")
    print("bases:")
    for item in result["bases"]:
        print(f"  - {item}")
    print(f"updated existing: {result['updated_existing']}")
    if "verification" in result:
        print("verification:")
        for key, value in result["verification"].items():
            print(f"  {key}: {value}")


if __name__ == "__main__":
    raise SystemExit(main())
