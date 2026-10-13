from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.request
import uuid
from contextlib import contextmanager
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

try:
    from tools.management_exclusion import assert_management_idle
except ModuleNotFoundError:
    from management_exclusion import assert_management_idle  # type: ignore

try:
    from tools.firecrawl_obsidian_pipeline import capture_url_api_first, parse_api_capture, validate_full_capture_body, PipelineStop
except ModuleNotFoundError:
    from firecrawl_obsidian_pipeline import capture_url_api_first, parse_api_capture, validate_full_capture_body, PipelineStop

try:
    from tools.case_evidence_contract import (
        ASSET_MODE_CASE,
        ASSET_MODE_FORMAL,
        MANIFEST_VERSION as CASE_MANIFEST_VERSION,
        MANIFEST_VERSION_V2 as CASE_MANIFEST_VERSION_V2,
        CaseEvidenceError,
        case_asset_paths,
        package_manifest as read_case_evidence_manifest,
        stage_manifest as stage_case_evidence_manifest,
        validate_manifest as validate_case_evidence_manifest,
        validate_selection_contract,
    )

except ModuleNotFoundError:
    from case_evidence_contract import (  # type: ignore
        ASSET_MODE_CASE,
        ASSET_MODE_FORMAL,
        MANIFEST_VERSION as CASE_MANIFEST_VERSION,
        MANIFEST_VERSION_V2 as CASE_MANIFEST_VERSION_V2,
        CaseEvidenceError,
        case_asset_paths,
        package_manifest as read_case_evidence_manifest,
        stage_manifest as stage_case_evidence_manifest,
        validate_manifest as validate_case_evidence_manifest,
        validate_selection_contract,
    )

try:
    from tools.ingest_plan_contract import VALID_ADOPTION_STATUSES
except ModuleNotFoundError:
    from ingest_plan_contract import VALID_ADOPTION_STATUSES  # type: ignore


try:
    from tools.obsidian_vault_adapter import project_concept_graph_for_sources
except ModuleNotFoundError:
    from obsidian_vault_adapter import project_concept_graph_for_sources  # type: ignore

try:
    from tools.workspace_paths import WorkspacePathError, vault_path, vault_relative_posix
except ModuleNotFoundError:
    from workspace_paths import WorkspacePathError, vault_path, vault_relative_posix  # type: ignore


def infographic_reverse_review_evidence_line() -> str:
    contract = INFOGRAPHIC_REVERSE_REVIEW_CONTRACT
    return (
        f"已按 {contract['normative_owner']} 的 INFOGRAPHIC_REVERSE_REVIEW_CONTRACT 完成"
        f"來源意思、可見文字逐項／逐句、關係與 footer / `{contract['required_footer_signature']}` 反審；"
        f"反例和失敗條件包括 {contract['blocking_failures']}，均未命中。"
        f" 規格全文讀回 receipt: {infographic_spec_read_receipt_text()}。"
    )


try:
    from tools.infographic_compiler import (
        AUTHORITATIVE_SOURCE_TITLE_SOURCES,
        canonical_infographic_asset_filename,
        compile_infographic_page,
        validate_content_map,
        validate_multi_page_orientation,
        validate_primary_work_metadata,
        validate_reader_metadata,
        validate_visual_structure,
        reader_infographic_title,
    )
    from tools.infographic_attempt_contract import (
        FORMAL_INFOGRAPHIC_MODEL,
        FORMAL_INFOGRAPHIC_PROVIDER,
        FORMAL_INFOGRAPHIC_GENERATION_REQUEST,
        IMAGEGEN_BLOCKING_FAILURES,
        IMAGEGEN_GENERATION_POLICY,
        IMAGEGEN_REPAIRABLE_FAILURES,
        INFOGRAPHIC_GENERATION_CONTRACT,
        V2_IMAGEGEN_ACTION_ENVELOPE_VERSION,
        V2_IMAGEGEN_ATTEMPT_LEDGER_VERSION,
        V2_IMAGEGEN_RESPONSE_VERSION,
    )
    from tools.infographic_prompt_contract import (
        blocking_repair_prompt,
        INFOGRAPHIC_PROMPT_CONTRACT_VERSION,
        INFOGRAPHIC_REVERSE_REVIEW_CONTRACT,
        INFOGRAPHIC_STANDALONE_AUDIENCE,
        INFOGRAPHIC_STANDALONE_PROMPT_MARKER,
        INFOGRAPHIC_TEMPLATE_VERSION,
        INFOGRAPHIC_VISIBLE_TEXT_MARKER,
        comparable_visible_text,
        footer_format_equivalent,
        image_semantic_readback_errors,
        accepted_footer_text,
        image_reverse_review_errors,
        quality_only_reverse_review_failure_errors,
        image_severe_failure_readback_errors,
        infographic_spec_read_receipt,
        infographic_spec_read_receipt_text,
        prompt_uses_semantic_readback_contract,
        visible_text_quality_errors,
        contextual_heading_labels,
        visible_text_from_prompt,
    )
    from tools.image_source_contract import (
        canonical_analysis_sha256,
        inspect_image,
        validate_visual_analysis,
    )
    from tools.path_budget import (
        PathBudgetError,
        assert_generated_path,
        bounded_slug,
        require_output_source_id,
    )
    from tools.source_identity import is_youtube_url, normalize_url as normalize_source_url
    from tools.youtube_transcript_capture import (
        ATTESTATION as YOUTUBE_SUBMITTED_URL_ATTESTATION,
        CAPTURE_POLICY as YOUTUBE_CAPTURE_POLICY,
        YoutubeCaptionsUnavailable,
        YoutubeTranscriptError,
        YoutubeTranscriptCapture,
        capture_youtube_transcript,
        vtt_to_canonical_body,
    )
    from tools.url_ingest_qc_router import execution_policy, EXECUTION_CONTRACT_VERSION
except ModuleNotFoundError:
    from infographic_compiler import (  # type: ignore
        AUTHORITATIVE_SOURCE_TITLE_SOURCES,
        canonical_infographic_asset_filename,
        compile_infographic_page,
        validate_content_map,
        validate_multi_page_orientation,
        validate_primary_work_metadata,
        validate_reader_metadata,
        validate_visual_structure,
        reader_infographic_title,
    )
    from infographic_attempt_contract import (  # type: ignore
        FORMAL_INFOGRAPHIC_MODEL,
        FORMAL_INFOGRAPHIC_PROVIDER,
        FORMAL_INFOGRAPHIC_GENERATION_REQUEST,
        IMAGEGEN_BLOCKING_FAILURES,
        IMAGEGEN_GENERATION_POLICY,
        IMAGEGEN_REPAIRABLE_FAILURES,
        INFOGRAPHIC_GENERATION_CONTRACT,
        V2_IMAGEGEN_ACTION_ENVELOPE_VERSION,
        V2_IMAGEGEN_ATTEMPT_LEDGER_VERSION,
        V2_IMAGEGEN_RESPONSE_VERSION,
    )
    from infographic_prompt_contract import (  # type: ignore
        blocking_repair_prompt,
        INFOGRAPHIC_PROMPT_CONTRACT_VERSION,
        INFOGRAPHIC_REVERSE_REVIEW_CONTRACT,
        INFOGRAPHIC_STANDALONE_AUDIENCE,
        INFOGRAPHIC_STANDALONE_PROMPT_MARKER,
        INFOGRAPHIC_TEMPLATE_VERSION,
        INFOGRAPHIC_VISIBLE_TEXT_MARKER,
        comparable_visible_text,
        footer_format_equivalent,
        image_semantic_readback_errors,
        accepted_footer_text,
        image_reverse_review_errors,
        quality_only_reverse_review_failure_errors,
        image_severe_failure_readback_errors,
        infographic_spec_read_receipt,
        infographic_spec_read_receipt_text,
        prompt_uses_semantic_readback_contract,
        visible_text_quality_errors,
        contextual_heading_labels,
        visible_text_from_prompt,
    )
    from image_source_contract import (  # type: ignore
        canonical_analysis_sha256,
        inspect_image,
        validate_visual_analysis,
    )
    from path_budget import (  # type: ignore
        PathBudgetError,
        assert_generated_path,
        bounded_slug,
        require_output_source_id,
    )
    from source_identity import is_youtube_url, normalize_url as normalize_source_url  # type: ignore
    from youtube_transcript_capture import (  # type: ignore
        ATTESTATION as YOUTUBE_SUBMITTED_URL_ATTESTATION,
        CAPTURE_POLICY as YOUTUBE_CAPTURE_POLICY,
        YoutubeCaptionsUnavailable,
        YoutubeTranscriptError,
        YoutubeTranscriptCapture,
        capture_youtube_transcript,
        vtt_to_canonical_body,
    )
    from url_ingest_qc_router import execution_policy, EXECUTION_CONTRACT_VERSION  # type: ignore


SCHEMA_VERSION = "compiler_worker_v2_receipt_v2"
JOB_SCHEMA_VERSION = "compiler_worker_v2_job_v1"
WORKER_ENVELOPE_VERSION = "compiler_worker_v2_worker_envelope_v1"
KP_SCHEMA_VERSION = "knowledge_package_v2"
COMPLETION_SCHEMA_VERSION = "compiler_worker_v2_completion_receipt_v1"
INFOGRAPHIC_REVISION_PLAN_VERSION = "infographic_revision_plan_v1"
CASE_REVALIDATION_PLAN_VERSION = "case_revalidation_plan_v1"
CASE_REVALIDATION_ORIGIN_VERSION = "case_revalidation_origin_v1"
REVISION_JOB_KINDS = frozenset({"infographic_revision", "incomplete_infographic_recovery", "case_revalidation"})
INFOGRAPHIC_CURRENT_POINTER_VERSION = "infographic_current_pointer_v1"
COVERAGE_AUDIT_SCHEMA_VERSION = "coverage_audit_v1"
INFOGRAPHIC_CORE_REVIEW_CONFIRMATION_SCHEMA_VERSION = "compiler_worker_v2_infographic_core_review_confirmation_v1"
INFOGRAPHIC_UNSTARTED_PROMPT_VERSION_MIGRATION_SCHEMA_VERSION = "compiler_worker_v2_unstarted_prompt_version_migration_v1"
DEFAULT_PACKAGE_ROOT = Path("outputs") / "compiler_worker_v2"
DEFAULT_JOB_ROOT = DEFAULT_PACKAGE_ROOT / "_jobs"
DISPATCH_SLA_SECONDS = 60
VALID_COVERAGE_DISPOSITIONS = {
    "source_claim",
    "knowledge_point",
    "summary",
    "infographic",
    "application",
    "relation",
    "omitted",
}
RETAINED_HIGH_VALUE_DISPOSITIONS = VALID_COVERAGE_DISPOSITIONS - {"summary"}
V2_INFOGRAPHIC_PROMPT_COMPILER = "tools/infographic_prompt_contract.py"
V2_INFOGRAPHIC_TEMPLATE_VERSION = INFOGRAPHIC_TEMPLATE_VERSION
IMPLEMENTATION_STATUS = "production_ready"
SUPPORTED_INPUT_KINDS = ("url", "user_provided_text", "user_provided_image")
URL_CAPTURE_API_FIRST = "api_first_html_fallback"
URL_CAPTURE_DIRECT_HTML_ONLY = "direct_html_only"
DIRECT_HTML_ONLY_CAPTURE_POLICY = "direct_html_only_v1"
IMPLEMENTED_STAGES = {
    "foreground_job_envelope": True,
    "direct_url_capture": True,
    "knowledge_package_validation": True,
    "infographic_receipt_validation": True,
    "infographic_attempt_ledger_enforcement": True,
    "codex_imagegen_adapter_envelope": True,
    "user_text_input_bundle": True,
    "user_image_input_bundle": True,
    "deterministic_projector": True,
    "vault_write_and_readback": True,
    "incremental_outcome_and_completion_receipt": True,
    "coverage_audit_readback": True,
    "pre_receipt_reader_quality_gate": True,
    "fast_path_execution_gate": True,
    "case_evidence_asset_mode": True,
}
ACTION_UNIT_MARKERS = (
    "使用",
    "運用",
    "開始",
    "建立",
    "改善",
    "撰寫",
    "提示",
    "檢查",
    "測試",
    "比較",
    "探索",
    "選出",
    "處理",
    "支援",
    "提供",
    "維護",
    "轉化",
    "辨識",
)
INFOGRAPHIC_CONTRACT_DEFECT_FAILURES = {"wrong_or_missing_locked_text"}
INFOGRAPHIC_CONTRACT_DEFECT_REOPEN_SCHEMA_VERSION = (
    "compiler_worker_v2_infographic_contract_defect_reopen_v1"
)
INFOGRAPHIC_QUALITY_RECONSIDERATION_SCHEMA_VERSION = (
    "compiler_worker_v2_infographic_quality_reconsideration_v1"
)
SECTION_HEADINGS_WITH_REQUIRED_CHILDREN = (
    "最佳做法",
    "最佳實務",
    "建議做法",
    "操作步驟",
    "使用方法",
    "方法",
    "實作方式",
    "工作法",
    "注意事項",
)
ARTICLE_END_MARKERS = {
    "延伸閱讀",
    "關鍵字：",
    "往下滑看下一篇文章",
    "即時熱門文章",
    "會員登入",
    "追蹤我們",
    "繼續閱讀",
    "條款與政策",
    "有興趣把 AI 帶入你的業務？",
    "Related posts",
    "Recommended posts",
    "Recommended articles",
    "More articles",
    "More from",
    "Subscribe",
    "Newsletter",
    "Get the developer newsletter",
    "Product updates, how-tos, community spotlights, and more. Delivered monthly to your inbox.",
    "Thank you! You’re subscribed.",
    "Sorry, there was a problem with your submission, please try again later.",
}
ARTICLE_CHROME_LINES = {
    "|",
    "分享",
    "收藏",
    "Share",
    "Copy link",
    "Copy as markdown",
    "Ask questions about this page",
    "No items found.",
    "Prev Prev",
    "Next Next",
    "Copy command to clipboard",
}


class CompilerWorkerV2Error(ValueError):
    """The v2 compiler package is stale, incomplete, or unsafe to project."""


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def normalize_url(url: str) -> str:
    try:
        return normalize_source_url(url)
    except ValueError as exc:
        raise CompilerWorkerV2Error(str(exc)) from exc


def slugify(value: str) -> str:
    value = value.lower()
    value = re.sub(r"[^a-z0-9]+", "_", value)
    value = re.sub(r"_+", "_", value).strip("_")
    return bounded_slug(value or "source")


def extract_title(raw_html: str) -> str:
    h1 = re.search(r"<h1[^>]*>(.*?)</h1>", raw_html, flags=re.I | re.S)
    if h1:
        return clean_text(h1.group(1)).strip()
    title = re.search(r"<title[^>]*>(.*?)</title>", raw_html, flags=re.I | re.S)
    if title:
        return clean_text(title.group(1)).split("|")[0].strip()
    return "Untitled source"


def clean_text(raw_html: str) -> str:
    text = re.sub(r"(?is)<script.*?</script>", "\n", raw_html)
    text = re.sub(r"(?is)<style.*?</style>", "\n", text)
    text = re.sub(r"(?is)<noscript.*?</noscript>", "\n", text)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</(p|div|li|h[1-6]|blockquote|section|article)>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    lines = []
    for line in text.splitlines():
        normalized = re.sub(r"\s+", " ", line).strip()
        if normalized:
            lines.append(normalized)
    return "\n".join(lines)


def extract_article_text(raw_html: str, title: str) -> str:
    """Prefer semantically dense article/content blocks when the page exposes them."""

    try:
        from bs4 import BeautifulSoup
    except ImportError as exc:
        raise CompilerWorkerV2Error(
            "URL capture requires the BeautifulSoup HTML parser to verify article body"
        ) from exc

    soup = BeautifulSoup(raw_html, "html.parser")
    for selector in (
        "script", "style", "noscript", "nav", '[role="navigation"]',
        "footer", "header", "form", "button", "svg",
    ):
        for element in soup.select(selector):
            element.decompose()

    def content_candidate(element: Any) -> dict[str, Any] | None:
        text = clean_text(str(element))
        text_length = len(text)
        if text_length < 120:
            return None
        link_text = clean_text(" ".join(str(link) for link in element.find_all("a")))
        link_ratio = len(link_text) / max(text_length, 1)
        if link_ratio > 0.45:
            return None
        return {
            "element": element,
            "text": text,
            "text_length": text_length,
            "link_ratio": link_ratio,
            "score": text_length * (1 - link_ratio),
        }

    candidates = []
    for element in soup.find_all(True):
        name = (element.name or "").lower()
        classes = " ".join(str(item) for item in element.get("class", []))
        role = str(element.get("role") or "")
        signal = " ".join((name, classes, role)).casefold()
        if not (
            name in {"article", "main"}
            or role.casefold() == "main"
            or re.search(r"\b(article|post|story|content|body|rich[-_ ]?text|entry)\b", signal)
        ):
            continue
        candidate = content_candidate(element)
        if candidate:
            candidates.append(candidate)

    if not candidates:
        # Plain HTML/fragment fallback must not restore removed navigation or
        # bypass the same content-density checks used for semantic blocks.
        fallback = content_candidate(soup.body or soup)
        if not fallback:
            raise CompilerWorkerV2Error(
                "URL capture lacks a readable article body after excluding navigation"
            )
        return crop_article_text(fallback["text"], title)

    kept = []
    sorted_candidates = sorted(candidates, key=lambda item: item["score"], reverse=True)
    for candidate in sorted_candidates:
        element = candidate["element"]
        if any(other["element"] in element.parents for other in kept):
            continue
        child_score = sum(
            other["score"]
            for other in sorted_candidates
            if other is not candidate and other["element"] in element.descendants
        )
        if child_score >= candidate["score"] * 0.55:
            continue
        kept.append(candidate)

    kept.sort(key=lambda item: getattr(item["element"], "sourceline", None) or raw_html.find(str(item["element"])[:80]))
    article = "\n".join(item["text"] for item in kept).strip()
    if title and title not in article.splitlines()[:3]:
        article = f"{title}\n{article}"
    return crop_article_text(article, title)


def crop_article_text(text: str, title: str) -> str:
    lines = text.splitlines()
    start = 0
    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped == title or stripped.endswith(f" {title}"):
            start = index
            break
    end = len(lines)
    for index, line in enumerate(lines[start + 1 :], start + 1):
        if line.strip() in ARTICLE_END_MARKERS:
            end = index
            break
    article_lines = []
    for line in lines[start:end]:
        stripped = line.strip()
        if stripped in ARTICLE_CHROME_LINES:
            continue
        if stripped.startswith(("{show=", "class=", "data-dl")):
            continue
        article_lines.append(stripped)
    article = "\n".join(article_lines).strip()
    if not article:
        raise CompilerWorkerV2Error("canonical article body is empty")
    return article


def validate_url_capture(
    *,
    status: int,
    content_type: str,
    raw_html: str,
    canonical_body: str,
    extracted_title: str,
) -> None:
    media_type = content_type.split(";", 1)[0].strip().lower()
    if status < 200 or status >= 300:
        raise CompilerWorkerV2Error(f"URL capture returned HTTP {status}")
    if media_type not in {"text/html", "application/xhtml+xml"}:
        raise CompilerWorkerV2Error(f"URL capture has unsupported content type: {media_type or 'missing'}")
    if extracted_title == "Untitled source":
        raise CompilerWorkerV2Error("URL capture lacks a source-declared title")
    try:
        validate_full_capture_body(canonical_body, raw_html)
    except PipelineStop as exc:
        raise CompilerWorkerV2Error("URL capture rejected: " + exc.message) from None
    # An extractor can return a plausible 200-character article while silently
    # losing a later paragraph. Check semantic article paragraphs against the
    # captured HTML before declaring the source complete.
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(raw_html, "html.parser")
    article = soup.find("article")
    if article is not None:
        normalized_body = " ".join(canonical_body.split()).casefold()
        for paragraph in article.find_all("p"):
            expected = " ".join(paragraph.get_text(" ", strip=True).split()).casefold()
            if len(expected) >= 40 and expected not in normalized_body:
                raise CompilerWorkerV2Error(
                    "URL capture is partial_capture: a readable article paragraph is missing from the canonical body"
                )


def validated_identifier(value: str, label: str) -> str:
    normalized = str(value).strip()
    if (
        not normalized
        or normalized in {".", ".."}
        or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", normalized)
    ):
        raise CompilerWorkerV2Error(f"invalid {label}")
    if label == "source_id":
        try:
            require_output_source_id(normalized, label=label)
        except PathBudgetError as exc:
            raise CompilerWorkerV2Error(str(exc)) from exc
    return normalized


def output_slug(value: str) -> str:
    return bounded_slug(validated_identifier(value, "slug"))


def package_dir(root: Path, source_id: str, slug: str) -> Path:
    safe_source_id = validated_identifier(source_id, "source_id")
    safe_slug = output_slug(slug)
    package = relative_to_root(
        root / DEFAULT_PACKAGE_ROOT / f"{safe_source_id}_{safe_slug}",
        root,
        "package",
    )
    try:
        assert_generated_path(
            package / "assets" / f"{safe_source_id}_{safe_slug}_source_01.png",
            label="Compiler Worker infographic asset",
        )
    except PathBudgetError as exc:
        raise CompilerWorkerV2Error(str(exc)) from exc
    return package


def write_json(path: Path, payload: dict[str, Any]) -> None:
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    temporary = path.with_name(f".{uuid.uuid4().hex}.tmp")
    try:
        assert_generated_path(path, label="Compiler Worker JSON output")
        assert_generated_path(temporary, label="Compiler Worker temporary JSON output", reserve=0)
    except PathBudgetError as exc:
        raise CompilerWorkerV2Error(str(exc)) from exc
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def write_bytes(path: Path, payload: bytes) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        assert_generated_path(path, label="Compiler Worker binary output")
        assert_generated_path(temporary, label="Compiler Worker temporary binary output", reserve=0)
    except PathBudgetError as exc:
        raise CompilerWorkerV2Error(str(exc)) from exc
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary.write_bytes(payload)
    temporary.replace(path)


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def relative_to_root(path: Path, root: Path, label: str) -> Path:
    resolved = path.resolve(strict=False)
    try:
        resolved.relative_to(root.resolve())
    except ValueError as exc:
        raise CompilerWorkerV2Error(f"{label} path escapes project root") from exc
    return resolved


def package_relative_path(package: Path, raw_path: str, label: str) -> Path:
    if not raw_path:
        raise CompilerWorkerV2Error(f"missing {label}")
    candidate = (package / raw_path).resolve(strict=False)
    try:
        candidate.relative_to(package.resolve())
    except ValueError as exc:
        raise CompilerWorkerV2Error(f"{label} path escapes package") from exc
    return candidate


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def canonical_payload_sha256(payload: dict[str, Any]) -> str:
    unsigned = {key: value for key, value in payload.items() if key != "payload_sha256"}
    return sha256_text(json.dumps(unsigned, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


def image_attempt_ledger_path(package: Path) -> Path:
    return package / "imagegen_attempts.json"


def image_action_envelope_path(package: Path) -> Path:
    return package / "image_action_envelope.json"


def load_image_attempt_ledger(
    package: Path,
    source_identity: str,
    *,
    create: bool = False,
) -> dict[str, Any]:
    path = image_attempt_ledger_path(package)
    if not path.exists():
        if not create:
            raise CompilerWorkerV2Error("formal infographic requires imagegen_attempts.json")
        return {
            "schema_version": V2_IMAGEGEN_ATTEMPT_LEDGER_VERSION,
            "source_identity": source_identity,
            "generation_contract": INFOGRAPHIC_GENERATION_CONTRACT,
            "pages": {},
        }
    try:
        ledger = read_json(path)
    except (OSError, json.JSONDecodeError) as exc:
        raise CompilerWorkerV2Error("imagegen_attempts.json is unreadable") from exc
    if (
        ledger.get("schema_version") != V2_IMAGEGEN_ATTEMPT_LEDGER_VERSION
        or ledger.get("source_identity") != source_identity
        or ledger.get("generation_contract") != INFOGRAPHIC_GENERATION_CONTRACT
        or not isinstance(ledger.get("pages"), dict)
    ):
        raise CompilerWorkerV2Error("imagegen_attempts.json does not match this Knowledge Package")
    return ledger


def infographic_page(kp: dict[str, Any], page_id: str) -> dict[str, Any]:
    matches = [
        item
        for item in kp.get("infographics") or []
        if isinstance(item, dict) and item.get("page_id") == page_id
    ]
    if len(matches) != 1:
        raise CompilerWorkerV2Error("image action page_id must identify exactly one infographic")
    return matches[0]


def compiled_infographic_page(
    kp: dict[str, Any],
    package: Path,
    infographic: dict[str, Any],
) -> dict[str, Any]:
    infographics = [
        item for item in kp.get("infographics") or [] if isinstance(item, dict)
    ]
    try:
        page_index = next(
            index
            for index, item in enumerate(infographics, start=1)
            if item is infographic
        )
    except StopIteration as exc:
        raise CompilerWorkerV2Error("infographic is not part of this Knowledge Package") from exc
    source = kp.get("source") if isinstance(kp.get("source"), dict) else {}
    revision = kp.get("revision") if isinstance(kp.get("revision"), dict) else {}
    slug = str(revision.get("asset_slug") or "").strip()
    if not slug:
        slug = package.name.split("_", 1)[1] if "_" in package.name else ""
    try:
        return compile_infographic_page(
            source=source,
            infographic=infographic,
            slug=slug,
            page_index=page_index,
            page_count=len(infographics),
        )
    except ValueError as exc:
        raise CompilerWorkerV2Error(
            f"infographic {page_index} compile failed: {exc}"
        ) from exc


def validate_compiled_infographic_page(
    kp: dict[str, Any],
    package: Path,
    infographic: dict[str, Any],
) -> list[str]:
    page_id = str(infographic.get("page_id") or "").strip() or "unknown"
    try:
        compiled = compiled_infographic_page(kp, package, infographic)
    except CompilerWorkerV2Error as exc:
        return [str(exc)]
    # Preserve already locked artifacts across this exact layout-only compiler
    # correction. All content, identity, prompt and plan hashes still must match.
    current_layout = "使用手繪分區與非對稱平衡，保持一致的文字層級；分區按內容調整，避免完美等寬卡片。"
    if infographic.get("compiled_plan_sha256") != compiled["compiled_plan_sha256"] and current_layout in compiled["prompt"]:
        prior = dict(compiled)
        prior["prompt"] = prior["prompt"].replace(current_layout, "採用等寬卡片並保持一致層級。", 1)
        prior["compiled_plan_sha256"] = canonical_payload_sha256({key: value for key, value in prior.items() if key != "compiled_plan_sha256"})
        if infographic.get("compiled_plan_sha256") == prior["compiled_plan_sha256"]:
            compiled = prior
    errors: list[str] = []
    if infographic.get("compiled_plan_schema_version") != compiled["schema_version"]:
        errors.append(f"infographic {page_id} missing compiled plan schema receipt")
    if infographic.get("prompt_contract_version") != compiled["prompt_contract_version"]:
        errors.append(f"infographic {page_id} prompt contract receipt mismatch")
    if infographic.get("compiled_plan_sha256") != compiled["compiled_plan_sha256"]:
        errors.append(f"infographic {page_id} compiled plan hash mismatch")
    if infographic.get("asset_path") != compiled["asset_path"]:
        errors.append(f"infographic {page_id} compiled asset path mismatch")
    prompt_path_value = str(infographic.get("locked_prompt_path") or "").strip()
    if prompt_path_value:
        try:
            prompt_path = package_relative_path(
                package,
                prompt_path_value,
                "locked infographic prompt",
            )
        except CompilerWorkerV2Error as exc:
            errors.append(str(exc))
        else:
            if (
                not prompt_path.is_file()
                or prompt_path.read_text(encoding="utf-8").rstrip("\n")
                != compiled["prompt"]
            ):
                errors.append(f"infographic {page_id} compiled prompt mismatch")
    return errors


def locked_infographic_context(
    package: Path,
    infographic: dict[str, Any],
) -> tuple[Path, Path, str]:
    page_id = validated_identifier(str(infographic.get("page_id") or ""), "infographic page_id")
    prompt_path = package_relative_path(
        package,
        str(infographic.get("locked_prompt_path") or ""),
        "locked infographic prompt",
    )
    if not prompt_path.is_file():
        raise CompilerWorkerV2Error(f"infographic {page_id} locked prompt is missing")
    prompt = prompt_path.read_text(encoding="utf-8").rstrip("\n")
    lock_hash = sha256_text(prompt)
    if infographic.get("locked_prompt_sha256") != lock_hash:
        raise CompilerWorkerV2Error(f"infographic {page_id} locked prompt hash mismatch")
    if (
        INFOGRAPHIC_STANDALONE_PROMPT_MARKER not in prompt
        or INFOGRAPHIC_VISIBLE_TEXT_MARKER not in prompt
    ):
        raise CompilerWorkerV2Error(f"infographic {page_id} prompt lacks the standalone reader contract")
    asset_path = package_relative_path(
        package,
        str(infographic.get("asset_path") or ""),
        "infographic asset",
    )
    return prompt_path, asset_path, lock_hash


def attempt_record_sha256(record: dict[str, Any]) -> str:
    return canonical_payload_sha256(record)


def legacy_core_review_confirmation_errors(
    infographic: dict[str, Any],
    record: dict[str, Any],
    package: Path,
) -> list[str]:
    """Return current-core evidence gaps for one already-generated legacy page.

    A prompt upgrade must never silently rewrite an accepted PNG or erase its
    historic attempt trail.  The sole admission bridge is an independently
    bound review against the *current* core axes.  This helper is deliberately
    unusable for a current prompt: current pages already follow the normal
    imagegen completion contract.
    """

    if infographic_uses_current_prompt_contract(infographic, package):
        return ["page uses the current prompt contract"]
    try:
        prompt_path, asset_path, locked_prompt_sha256 = locked_infographic_context(
            package, infographic
        )
    except CompilerWorkerV2Error as exc:
        return [str(exc)]
    attempts = record.get("attempts")
    latest = attempts[-1] if isinstance(attempts, list) and attempts else None
    if not isinstance(latest, dict) or latest.get("status") != "accepted":
        return ["legacy page has no accepted latest image attempt"]
    if not asset_path.is_file():
        return ["legacy page asset is missing"]
    png_sha256 = sha256_bytes(asset_path.read_bytes())
    if latest.get("png_sha256") != png_sha256:
        return ["legacy page asset does not match its latest attempt"]
    confirmation = latest.get("core_review_confirmation")
    if not isinstance(confirmation, dict):
        return ["legacy page lacks a current core review confirmation"]
    errors: list[str] = []
    if (
        confirmation.get("schema_version")
        != INFOGRAPHIC_CORE_REVIEW_CONFIRMATION_SCHEMA_VERSION
        or confirmation.get("reason") != "current_core_review_confirmation_v1"
    ):
        errors.append("legacy current core review confirmation schema is invalid")
    if not re.fullmatch(
        r"[0-9a-f]{64}", str(confirmation.get("review_file_sha256") or "")
    ):
        errors.append("legacy current core review confirmation lacks a review-file hash")
    semantic = confirmation.get("semantic_readback")
    prompt = prompt_path.read_text(encoding="utf-8").rstrip("\n")
    errors.extend(
        image_semantic_readback_errors(
            semantic if isinstance(semantic, dict) else None,
            png_sha256=png_sha256,
            prompt=prompt,
        )
    )
    errors.extend(image_reverse_review_errors(
        semantic if isinstance(semantic, dict) else None,
        allow_historical_spec_receipt=True,
    ))
    if (
        isinstance(semantic, dict)
        and semantic.get("inspected_prompt_sha256") != locked_prompt_sha256
    ):
        errors.append("legacy current core review does not bind the locked prompt")
    return errors


def legacy_page_has_current_core_confirmation(
    kp: dict[str, Any],
    infographic: dict[str, Any],
    package: Path,
) -> bool:
    """Check the narrow legacy-admission bridge without relaxing new pages."""

    ledger_path = image_attempt_ledger_path(package)
    if not ledger_path.is_file():
        return False
    source = kp.get("source") if isinstance(kp.get("source"), dict) else {}
    try:
        ledger = load_image_attempt_ledger(
            package, str(source.get("source_identity") or "")
        )
    except CompilerWorkerV2Error:
        return False
    pages = ledger.get("pages")
    record = pages.get(str(infographic.get("page_id") or "")) if isinstance(pages, dict) else None
    return isinstance(record, dict) and not legacy_core_review_confirmation_errors(
        infographic, record, package
    )


def validate_image_attempt_ledger(kp: dict[str, Any], package: Path) -> list[str]:
    infographics = [
        item for item in kp.get("infographics") or [] if isinstance(item, dict)
    ]
    if not infographics:
        return []
    ledger_path = image_attempt_ledger_path(package)
    if not ledger_path.exists():
        # Immutable receipts created before v2 adopted the executable ledger
        # remain replayable; they cannot authorize a new write or generation.
        if (package / "completion_receipt.json").is_file():
            return []
        return ["formal infographic requires imagegen_attempts.json"]
    source = kp.get("source") if isinstance(kp.get("source"), dict) else {}
    try:
        ledger = load_image_attempt_ledger(package, str(source.get("source_identity") or ""))
    except CompilerWorkerV2Error as exc:
        return [str(exc)]
    pages = ledger["pages"]
    assert isinstance(pages, dict)
    expected_page_ids = {
        str(item.get("page_id") or "") for item in infographics if item.get("page_id")
    }
    if set(pages) != expected_page_ids:
        return ["imagegen attempt ledger pages do not match the Knowledge Package"]
    errors: list[str] = []
    max_attempts = int(IMAGEGEN_GENERATION_POLICY["max_candidates_per_asset"])
    for infographic in infographics:
        page_id = str(infographic.get("page_id") or "")
        record = pages.get(page_id)
        if not isinstance(record, dict):
            errors.append(f"infographic {page_id} has no attempt record")
            continue
        try:
            _, asset_path, lock_hash = locked_infographic_context(package, infographic)
        except CompilerWorkerV2Error as exc:
            errors.append(str(exc))
            continue
        attempts = record.get("attempts")
        if (
            record.get("page_id") != page_id
            or record.get("locked_prompt_sha256") != lock_hash
            or record.get("asset_path") != asset_path.relative_to(package).as_posix()
            or not isinstance(attempts, list)
        ):
            errors.append(f"infographic {page_id} attempt record does not match its locked page")
            continue
        is_text_revision = (kp.get("revision") or {}).get("mode") == "same_job_text_revision"
        if is_text_revision:
            try:
                text_revision.validate_record(sys.modules[__name__], text_revision.workspace(package), package, record)
            except (CompilerWorkerV2Error, ValueError, KeyError) as exc:
                errors.append(str(exc))
                continue
            latest = attempts[-1]
        else:
            if len(attempts) == max_attempts + 1:
                correction = attempts[-1] if isinstance(attempts[-1], dict) else {}
                correction_errors = dispatch_correction_authorization_errors(
                    kp, page_id, attempts[:-1], correction.get("dispatch_correction_authorization"))
                if (correction_errors or correction.get("attempt_number") != 3
                        or correction.get("attempt_kind") != "blocking_repair"
                        or correction.get("failure_code") != attempts[-2].get("failure_code")):
                    errors.extend(correction_errors or ["invalid dispatch correction attempt"])
                    continue
                if correction.get("status") == "accepted":
                    prompt_path, _, _ = locked_infographic_context(package, infographic)
                    errors.extend(blocking_repair_receipt_errors(
                        correction, attempts[-2], prompt_path.read_text(encoding="utf-8").rstrip("\n")))
            elif not 1 <= len(attempts) <= max_attempts:
                errors.append(f"infographic {page_id} exceeds the formal image attempt limit")
                continue
            first = attempts[0]
            first_kind = first.get("attempt_kind") if isinstance(first, dict) else ""
            if (
                not isinstance(first, dict)
                or first.get("attempt_number") != 1
                or first_kind not in {"initial_generation", "contract_defect_repair"}
            ):
                errors.append(f"infographic {page_id} first attempt is not an allowed generation start")
                continue
            if first_kind == "contract_defect_repair" and not sorted(
                package.glob("imagegen_contract_defect_reopen_*.json")
            ):
                errors.append(f"infographic {page_id} contract defect repair lacks reopen receipt")
                continue
            latest = attempts[-1] if isinstance(attempts, list) and attempts else None
            legacy_admission = (
                not infographic_uses_current_prompt_contract(infographic, package)
                and not legacy_core_review_confirmation_errors(infographic, record, package)
            )
            if len(attempts) >= 2 and not legacy_admission:
                second = attempts[1]
                if (
                    not isinstance(second, dict)
                    or second.get("attempt_number") != 2
                    or second.get("attempt_kind") not in {"blocking_repair", "user_authorized_visual_repair"}
                    or first.get("status") != "blocking_failure"
                    or first.get("failure_code") not in IMAGEGEN_BLOCKING_FAILURES
                    or second.get("failure_code") != first.get("failure_code")
                ):
                    errors.append(f"infographic {page_id} repair lacks one recorded strict blocking failure")
                    continue
                if second.get("attempt_kind") == "user_authorized_visual_repair":
                    try:
                        validate_visual_repair_authorization(package, kp, infographic, first, second.get("repair_authorization"))
                    except CompilerWorkerV2Error as exc:
                        errors.append(str(exc))
                    review = (second.get("semantic_readback") or {}).get("restricted_edit_review", {})
                    if second.get("status") == "accepted" and (
                        review.get("all_text_preserved") is not True
                        or review.get("only_authorized_icons_removed") is not True
                        or review.get("reference_png_sha256") != first.get("png_sha256")
                        or not second.get("edit_prompt_sha256")
                    ):
                        errors.append("accepted restricted edit lacks bound preservation review")
                elif first.get("failure_code") not in IMAGEGEN_REPAIRABLE_FAILURES:
                    errors.append("ordinary repair requires a repairable severe content failure")
                elif second.get("repair_dispatch_contract") is not None and second.get("status") == "accepted":
                    prompt_path, _, _ = locked_infographic_context(package, infographic)
                    errors.extend(blocking_repair_receipt_errors(
                        second, first, prompt_path.read_text(encoding="utf-8").rstrip("\n")))
        if isinstance(latest, dict):
            errors.extend(footer_variance_binding_errors(latest.get("semantic_readback"), kp, page_id, latest.get("action_identity")))
        if not isinstance(latest, dict) or latest.get("status") != "accepted":
            errors.append(f"infographic {page_id} has no accepted final attempt")
            continue
        if (
            latest.get("provider") != FORMAL_INFOGRAPHIC_PROVIDER
            or latest.get("model") != FORMAL_INFOGRAPHIC_MODEL
            or latest.get("visual_qc_status") != "ready"
            or latest.get("png_sha256") != infographic.get("png_sha256")
            or latest.get("action_identity") != infographic.get("action_identity")
            or len(str(latest.get("dispatch_evidence") or "")) < 8
            or infographic.get("generation_attempt_count") != text_revision.count(record)
            or infographic.get("generation_attempt_kind") != latest.get("attempt_kind")
            or infographic.get("attempt_record_sha256") != attempt_record_sha256(record)
            or infographic.get("attempt_ledger_path") != "imagegen_attempts.json"
        ):
            errors.append(f"infographic {page_id} final receipt does not match its attempt ledger")
    return errors


def job_root(root: Path) -> Path:
    return root / DEFAULT_JOB_ROOT


@contextmanager
def compiler_process_lock(root: Path):
    lock_path = job_root(root) / ".compiler.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+b") as handle:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
        handle.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def job_file(root: Path, job_id: str) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", job_id):
        raise CompilerWorkerV2Error("invalid job_id")
    return job_root(root) / f"{job_id}.json"


def latest_v2_job_for_source(root: Path, source_identity: str) -> dict[str, Any] | None:
    directory = job_root(root)
    if not directory.exists():
        return None
    matches: list[dict[str, Any]] = []
    for path in sorted(directory.glob("*.json")):
        try:
            job = read_json(path)
        except json.JSONDecodeError:
            continue
        if (
            job.get("source_identity") == source_identity
            and job.get("job_kind") not in REVISION_JOB_KINDS
        ):
            matches.append(job)
    if len(matches) > 1:
        raise CompilerWorkerV2Error("multiple v2 jobs own the same source identity")
    return matches[0] if matches else None


def reserved_source_id_conflict(root: Path, source_id: str, source_identity: str, job_id: str) -> str:
    """Reject one human-facing source ID for two different immutable sources."""
    try:
        registry = vault_path(root) / "_registry" / "sources.jsonl"
    except WorkspacePathError as exc:
        raise CompilerWorkerV2Error(str(exc)) from exc
    if registry.is_file():
        for line_no, line in enumerate(registry.read_text(encoding="utf-8-sig").splitlines(), start=1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise CompilerWorkerV2Error(f"source registry JSON is invalid at line {line_no}") from exc
            if str(row.get("source_id") or "") == source_id and str(row.get("source_identity") or "") != source_identity:
                return f"source_id {source_id} is already reserved by a different registry source"
    directory = job_root(root)
    if directory.is_dir():
        for path in directory.glob("*.json"):
            job = read_json(path)
            if (
                str(job.get("job_id") or "") != job_id
                and job.get("job_kind") not in REVISION_JOB_KINDS
                and str(job.get("source_id") or "") == source_id
                and str(job.get("source_identity") or "") != source_identity
            ):
                return f"source_id {source_id} is already reserved by job {job.get('job_id')}"
    return ""


def write_v2_job(root: Path, job: dict[str, Any]) -> None:
    path = job_file(root, str(job["job_id"]))
    path.parent.mkdir(parents=True, exist_ok=True)
    write_json(path, job)


def ensure_accepted_job(
    *,
    root: Path,
    job_id: str,
    source_id: str,
    source_input_kind: str,
    source_identity: str,
    source_url: str = "",
    worker_runtime: str = "direct",
) -> dict[str, Any]:
    source_id = validated_identifier(source_id, "source_id")
    source_id_conflict = reserved_source_id_conflict(root, source_id, source_identity, job_id)
    if source_id_conflict:
        raise CompilerWorkerV2Error(source_id_conflict)
    existing_owner = latest_v2_job_for_source(root, source_identity)
    if existing_owner and str(existing_owner.get("job_id") or "") != job_id:
        raise CompilerWorkerV2Error(
            f"source identity is already owned by job {existing_owner.get('job_id')}"
        )
    path = job_file(root, job_id)
    if path.is_file():
        job = read_json(path)
        for field, expected in {
            "source_id": source_id,
            "source_input_kind": source_input_kind,
            "source_identity": source_identity,
        }.items():
            if str(job.get(field) or "") != expected:
                raise CompilerWorkerV2Error(f"accepted v2 job {field} mismatch")
        return job
    now = iso_now()
    job = {
        "schema_version": JOB_SCHEMA_VERSION,
        "execution_policy_version": EXECUTION_CONTRACT_VERSION,
        "job_id": job_id,
        "source_id": source_id,
        "source_input_kind": source_input_kind,
        "source_identity": source_identity,
        "status": "accepted",
        "first_accepted_at": now,
        "updated_at": now,
        "dispatch_sla_seconds": DISPATCH_SLA_SECONDS,
        "worker_runtime": worker_runtime,
        "worker_envelope_path": "",
        "dispatch_request_path": "",
        "worker_instance_id": "",
        "worker_dispatch_evidence": "",
    }
    if source_url:
        job["source_url"] = source_url
        job["normalized_url"] = source_url
        job["source_url_hash"] = source_identity.split(":", 1)[1]
    write_v2_job(root, job)
    return job


def stage_accepted_input(root: Path, job: dict[str, Any], source_path: Path, source_bytes: bytes) -> dict[str, Any]:
    suffix = source_path.suffix.lower() or ".bin"
    staged = relative_to_root(
        job_root(root) / "accepted-inputs" / f"{job['job_id']}{suffix}",
        root,
        "accepted input",
    )
    write_bytes(staged, source_bytes)
    if sha256_bytes(staged.read_bytes()) != job.get("source_identity", "").split(":", 1)[-1]:
        raise CompilerWorkerV2Error("accepted input staging hash mismatch")
    job.update({
        "accepted_input_path": staged.relative_to(root).as_posix(),
        "accepted_input_sha256": sha256_bytes(source_bytes),
        "updated_at": iso_now(),
    })
    write_v2_job(root, job)
    return job


def command_accept_job(args: argparse.Namespace) -> int:
    root = args.root.resolve()
    source_id = validated_identifier(args.source_id, "source_id")
    normalized_url = ""
    if args.url:
        normalized_url = normalize_url(args.url)
        source_input_kind = "url"
        source_hash = sha256_text(normalized_url)
    else:
        input_path = relative_to_root(args.text_file or args.image_file, root, "source input")
        if not input_path.is_file():
            raise CompilerWorkerV2Error("source input file is missing")
        source_input_kind = "user_provided_text" if args.text_file else "user_provided_image"
        source_bytes = input_path.read_bytes()
        source_hash = sha256_bytes(source_bytes)
    source_identity = f"{source_input_kind}:{source_hash}"
    existing = latest_v2_job_for_source(root, source_identity)
    if existing:
        repaired = False
        if not args.url:
            staged_value = str(existing.get("accepted_input_path") or "")
            staged_path = relative_to_root(root / staged_value, root, "accepted input") if staged_value else None
            if (
                staged_path is None
                or not staged_path.is_file()
                or sha256_bytes(staged_path.read_bytes()) != source_hash
            ):
                if existing.get("status") == "completed":
                    raise CompilerWorkerV2Error("completed v2 job accepted-input readback mismatch")
                stage_accepted_input(root, existing, input_path, source_bytes)
                repaired = True
        print(json.dumps({
            "status": existing.get("status"),
            "job_id": existing.get("job_id"),
            "source_identity": source_identity,
            "write_performed": repaired,
            "duplicate_job": True,
        }, ensure_ascii=False))
        return 0
    job_id = args.job_id or f"CW2-{source_hash[:16]}"
    job = ensure_accepted_job(
        root=root,
        job_id=job_id,
        source_id=source_id,
        source_input_kind=source_input_kind,
        source_identity=source_identity,
        source_url=normalized_url,
        worker_runtime=args.worker_runtime,
    )
    if not args.url:
        stage_accepted_input(root, job, input_path, source_bytes)
    print(json.dumps({
        "status": "accepted",
        "job_id": job_id,
        "source_identity": source_identity,
        "dispatch_sla_seconds": DISPATCH_SLA_SECONDS,
        "write_performed": True,
        "duplicate_job": False,
    }, ensure_ascii=False))
    return 0


def command_dispatch_job(args: argparse.Namespace) -> int:
    root = args.root.resolve()
    path = job_file(root, args.job_id)
    if not path.exists():
        raise CompilerWorkerV2Error("v2 job not found")
    job = read_json(path)
    if job.get("status") not in {"accepted", "queued"}:
        print(json.dumps({"status": job.get("status"), "job_id": args.job_id, "write_performed": False}, ensure_ascii=False))
        return 0
    worker_claim = job.get("worker_claim") or f"cw2_claim_{uuid.uuid4().hex}"
    envelope = {
        "tool": "compiler-worker-v2",
        "contract_version": WORKER_ENVELOPE_VERSION,
        "project_root": str(root),
        "job_id": job["job_id"],
        "source_id": job["source_id"],
        "source_input_kind": job["source_input_kind"],
        "source_url": job.get("source_url", ""),
        "normalized_url": job.get("normalized_url", ""),
        "accepted_input_path": job.get("accepted_input_path", ""),
        "accepted_input_sha256": job.get("accepted_input_sha256", ""),
        "source_identity": job["source_identity"],
        "worker_runtime": job["worker_runtime"],
        "worker_task": "run-compiler-worker-v2-source",
        "worker_claim": worker_claim,
        "runbook_path": "docs/Compiler Worker與Knowledge Package設計及驗收規格.md",
        "first_accepted_at": job["first_accepted_at"],
        "payload_sha256": "",
    }
    envelope["payload_sha256"] = canonical_payload_sha256(envelope)
    envelope_path = job_root(root) / "worker-envelopes" / f"{args.job_id}.json"
    dispatch_path = job_root(root) / "worker-dispatches" / f"{args.job_id}.json"
    write_json(envelope_path, envelope)
    write_json(dispatch_path, {
        "schema_version": "compiler_worker_v2_dispatch_request_v1",
        "job_id": args.job_id,
        "worker_runtime": job["worker_runtime"],
        "worker_envelope_path": envelope_path.relative_to(root).as_posix(),
        "created_at": iso_now(),
    })
    job.update({
        "status": "queued",
        "updated_at": iso_now(),
        "worker_claim": worker_claim,
        "worker_envelope_path": envelope_path.relative_to(root).as_posix(),
        "dispatch_request_path": dispatch_path.relative_to(root).as_posix(),
    })
    write_v2_job(root, job)
    print(json.dumps({
        "status": "queued",
        "job_id": args.job_id,
        "worker_runtime": job["worker_runtime"],
        "worker_envelope": str(envelope_path),
        "dispatch_request": str(dispatch_path),
        "worker_claim": worker_claim,
        "write_performed": True,
    }, ensure_ascii=False))
    return 0


def command_bind_worker(args: argparse.Namespace) -> int:
    root = args.root.resolve()
    path = job_file(root, args.job_id)
    if not path.exists():
        raise CompilerWorkerV2Error("v2 job not found")
    job = read_json(path)
    if job.get("status") != "queued":
        raise CompilerWorkerV2Error("v2 job must be queued before binding a worker")
    job.update({
        "status": "running",
        "updated_at": iso_now(),
        "worker_instance_id": args.worker_instance_id,
        "worker_dispatch_evidence": args.dispatch_evidence,
    })
    write_v2_job(root, job)
    print(json.dumps({
        "status": "running",
        "job_id": args.job_id,
        "worker_instance_id": args.worker_instance_id,
        "background_dispatch_bound": True,
    }, ensure_ascii=False))
    return 0


def command_worker_readback(args: argparse.Namespace) -> int:
    root = args.root.resolve()
    envelope_path = (args.worker_envelope if args.worker_envelope.is_absolute() else root / args.worker_envelope).resolve(strict=False)
    try:
        envelope_path.relative_to(root)
    except ValueError as exc:
        raise CompilerWorkerV2Error("worker envelope path escapes project root") from exc
    envelope = read_json(envelope_path)
    if envelope.get("tool") != "compiler-worker-v2" or envelope.get("contract_version") != WORKER_ENVELOPE_VERSION:
        raise CompilerWorkerV2Error("unsupported worker envelope")
    if envelope.get("payload_sha256") != canonical_payload_sha256(envelope):
        raise CompilerWorkerV2Error("worker envelope payload hash mismatch")
    job = read_json(job_file(root, str(envelope["job_id"])))
    if job.get("source_identity") != envelope.get("source_identity"):
        raise CompilerWorkerV2Error("worker envelope source identity mismatch")
    print(json.dumps({
        "status": job.get("status"),
        "job_id": job.get("job_id"),
        "source_id": job.get("source_id"),
        "source_identity": job.get("source_identity"),
        "source_url": job.get("source_url"),
        "worker_runtime": job.get("worker_runtime"),
        "worker_claim": envelope.get("worker_claim"),
        "background_dispatch_bound": bool(job.get("worker_instance_id")),
        "worker_instance_id": job.get("worker_instance_id", ""),
        "next_action": (
            "bind the queued worker" if job.get("status") == "queued" and not job.get("worker_instance_id")
            else "resume project with the bound Knowledge Package and verify the interrupted commit"
            if job.get("status") == "committing"
            else "resume the authorized image action, or authorize the one eligible locked page"
            if job.get("status") == "awaiting_imagegen"
            else "build or resume the source Input Bundle and Knowledge Package, then run project --write-vault"
            if job.get("status") in {"accepted", "queued", "running", "blocked"}
            else "read the immutable completion receipt; completed jobs are zero-write"
        ),
    }, ensure_ascii=False))
    return 0


def command_capabilities(_args: argparse.Namespace) -> int:
    print(json.dumps({
        "schema_version": "compiler_worker_v2_capabilities_v1",
        "implementation_status": IMPLEMENTATION_STATUS,
        "supported_input_kinds": list(SUPPORTED_INPUT_KINDS),
        "implemented_stages": IMPLEMENTED_STAGES,
        "image_generation_policy": IMAGEGEN_GENERATION_POLICY,
        "image_generation_commands": [
            "compile-infographics",
            "imagegen-authorize",
            "Codex imagegen",
            "imagegen-complete",
            "imagegen-invalidate",
        ],
        "infographic_revision_command": "prepare-infographic-revision",
        "production_entry_ready": all(IMPLEMENTED_STAGES.values()),
        "normal_ingest_entry": True,
        "normal_work_order_entry": "tools/ingest_rail.py start",
        "semantic_schema_entry": "tools/ingest_rail.py schema",
        "retired_commands": ["rebind-existing-completion"],
    }, ensure_ascii=False))
    return 0


def visual_recovery_context(
    *,
    root: Path,
    original_kp_path: Path,
    original_kp: dict[str, Any],
    original_package: Path,
    recovery: dict[str, Any],
) -> dict[str, str]:
    """Prove a pre-writer visual failure before creating a fresh revision."""
    original_job = validate_job_package_binding(root, original_kp_path, original_kp)
    writer_fields = [key for key in original_job if key == "commit_stage" or key.startswith("commit_")]
    if original_job.get("status") in {"committing", "completed"} or writer_fields:
        raise CompilerWorkerV2Error("visual recovery is limited to a package with no writer checkpoint")
    failed_page_id = str(recovery.get("failed_page_id") or "")
    if not failed_page_id:
        raise CompilerWorkerV2Error("visual recovery requires its exhausted page")
    ledger_path = image_attempt_ledger_path(original_package)
    source = original_kp.get("source") if isinstance(original_kp.get("source"), dict) else {}
    if not ledger_path.is_file():
        raise CompilerWorkerV2Error("visual recovery requires an image attempt ledger")
    ledger = load_image_attempt_ledger(original_package, str(source.get("source_identity") or ""))
    record = ledger["pages"].get(failed_page_id)
    original_page = infographic_page(original_kp, failed_page_id)
    _, asset_path, locked_prompt_sha256 = locked_infographic_context(original_package, original_page)
    attempts = record.get("attempts") if isinstance(record, dict) else None
    expected_attempts = int(IMAGEGEN_GENERATION_POLICY["max_candidates_per_asset"])
    if (not isinstance(record, dict) or record.get("page_id") != failed_page_id
            or record.get("locked_prompt_sha256") != locked_prompt_sha256
            or record.get("asset_path") != asset_path.relative_to(original_package).as_posix()
            or not isinstance(attempts, list) or len(attempts) != expected_attempts
            or any(not isinstance(item, dict) for item in attempts)):
        raise CompilerWorkerV2Error("visual recovery requires exactly the locked page's exhausted attempt history")
    first, second = attempts
    if (first.get("attempt_number") != 1 or first.get("attempt_kind") != "initial_generation"
            or second.get("attempt_number") != 2 or second.get("attempt_kind") != "user_authorized_visual_repair"):
        raise CompilerWorkerV2Error("visual recovery requires an initial generation followed by the one authorized visual repair")
    for attempt in attempts:
        semantic = attempt.get("semantic_readback")
        if (attempt.get("status") != "blocking_failure" or attempt.get("failure_code") != "reverse_review_failed"
                or not str(attempt.get("png_sha256") or "") or not isinstance(semantic, dict)
                or semantic.get("formal_ingestion_allowed") is not False
                or semantic.get("inspected_png_sha256") != attempt.get("png_sha256")
                or semantic.get("inspected_prompt_sha256") != locked_prompt_sha256
                or not str(semantic.get("reader_review") or "").strip()):
            raise CompilerWorkerV2Error("visual recovery requires two hash-bound independent reverse-review failures")
    reference = validate_visual_repair_authorization(original_package, original_kp, original_page, first, second.get("repair_authorization"))
    if sha256_bytes(reference.read_bytes()) != first.get("png_sha256"):
        raise CompilerWorkerV2Error("visual recovery first failure reference hash mismatch")
    if not asset_path.is_file() or sha256_bytes(asset_path.read_bytes()) != second.get("png_sha256"):
        raise CompilerWorkerV2Error("visual recovery second failure PNG is not preserved")
    return {
        "failed_page_id": failed_page_id,
        "failure_code": "reverse_review_failed",
        "first_attempt_id": str(first.get("attempt_id") or ""),
        "second_attempt_id": str(second.get("attempt_id") or ""),
        "original_attempt_ledger_path": ledger_path.relative_to(root).as_posix(),
        "original_attempt_ledger_sha256": sha256_bytes(ledger_path.read_bytes()),
    }


try:
    from tools import infographic_revision as text_revision
except ModuleNotFoundError:
    import infographic_revision as text_revision


def mirror_revision_input_evidence(
    original_package: Path,
    target: Path,
    *,
    job_id: str,
    source: dict[str, Any],
) -> bool:
    """Carry the accepted ingestion input forward for a revision package.

    A revision has a new job identity, but it must retain the original source
    evidence so that the normal ingest rail can project it through the one
    writer.  The copied bundle is the only intentionally rewritten file: its
    job id is rebound to the revision job while its source identity stays
    immutable.  Existing files must be byte-identical; this makes a repeated
    prepare command a safe recovery path instead of a hidden overwrite.
    """

    original_bundle_path = original_package / "input_bundle.json"
    if not original_bundle_path.is_file():
        # Older direct-worker packages can still be projected directly.  They
        # do not have an ingest-rail bundle to mirror.
        return False
    original_bundle = read_json(original_bundle_path)
    if not isinstance(original_bundle, dict) or original_bundle.get("status") != "accepted":
        raise CompilerWorkerV2Error("infographic revision requires an accepted original input bundle")
    for field in ("source_id", "source_identity", "source_input_kind"):
        if original_bundle.get(field) != source.get(field):
            raise CompilerWorkerV2Error(
                "original input bundle does not match the infographic revision source"
            )

    changed = False

    def mirror_file(relative_value: str, label: str) -> None:
        nonlocal changed
        source_path = package_relative_path(original_package, relative_value, label)
        if not source_path.is_file():
            raise CompilerWorkerV2Error(f"revision source evidence is missing: {relative_value}")
        safe_relative = source_path.relative_to(original_package)
        destination = target / safe_relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            if not destination.is_file() or sha256_bytes(destination.read_bytes()) != sha256_bytes(source_path.read_bytes()):
                raise CompilerWorkerV2Error(
                    f"revision source evidence differs from the accepted original: {relative_value}"
                )
            return
        shutil.copy2(source_path, destination)
        changed = True

    for filename in (
        "canonical_source.md",
        "source_capture.md",
        "source_capture_metadata.json",
        "review_packet.json",
        "image_analysis.json",
    ):
        source_path = original_package / filename
        if source_path.is_file():
            mirror_file(filename, "revision source evidence")

    referenced_paths = {
        str(original_bundle.get(key) or "")
        for key in (
            "youtube_caption_path",
            "youtube_info_path",
            "raw_html_capture_path",
            "api_capture_path",
            "original_image_path",
        )
        if str(original_bundle.get(key) or "")
    }
    metadata_path = original_package / "source_capture_metadata.json"
    if metadata_path.is_file():
        metadata = read_json(metadata_path)
        if isinstance(metadata, dict) and str(metadata.get("original_image_path") or ""):
            referenced_paths.add(str(metadata["original_image_path"]))
    for relative_value in sorted(referenced_paths):
        mirror_file(relative_value, "revision source evidence")

    revision_bundle = json.loads(json.dumps(original_bundle, ensure_ascii=False))
    revision_bundle["job_id"] = job_id
    target_bundle_path = target / "input_bundle.json"
    if target_bundle_path.exists():
        if read_json(target_bundle_path) != revision_bundle:
            raise CompilerWorkerV2Error(
                "revision input bundle differs from the accepted source evidence"
            )
    else:
        write_json(target_bundle_path, revision_bundle)
        changed = True
    return changed


def _incomplete_recovery_scope_errors(
    root: Path,
    package: Path,
    kp: dict[str, Any],
    job: dict[str, Any],
    ledger: dict[str, Any],
    recovery: dict[str, Any],
) -> list[str]:
    """Reject page recovery unless its shared, already-locked context remains intact."""

    errors: list[str] = []
    source = kp.get("source") if isinstance(kp.get("source"), dict) else {}
    canonical = package / "canonical_source.md"
    if not canonical.is_file() or source.get("canonical_body_sha256") != sha256_text(canonical.read_text(encoding="utf-8").rstrip("\n")):
        errors.append("canonical source identity changed")
    if job.get("job_id") != kp.get("job_id") or job.get("first_accepted_at") in {None, ""}:
        errors.append("original job identity or first accepted time is unavailable")
    execution = job.get("execution_contract")
    if not isinstance(execution, dict) or not isinstance(execution.get("scope"), dict):
        errors.append("original locked ingest scope is unavailable")
    else:
        current_scope = ingest_plan_scope(job, kp)
        locked_scope = execution["scope"]
        if "semantic_content_sha256" not in locked_scope:
            errors.append("original locked ingest scope lacks semantic source metadata binding")
        for field in ("source_identity", "canonical_body_sha256", "source_profile", "semantic_content_sha256"):
            if field in locked_scope and locked_scope.get(field) != current_scope.get(field):
                errors.append("shared source metadata differs from the locked ingest scope")
                break
    pages = kp.get("infographics")
    if not isinstance(pages, list) or not pages:
        return [*errors, "original infographic page set is unavailable"]
    page_ids = [str(page.get("page_id") or "") for page in pages if isinstance(page, dict)]
    if len(page_ids) != len(pages) or not all(page_ids) or len(set(page_ids)) != len(page_ids):
        errors.append("original infographic page set is invalid")
        return errors
    scope = recovery.get("locked_scope")
    if not isinstance(scope, dict):
        return [*errors, "incomplete recovery must bind canonical source, page order, title and footer scope"]
    if scope.get("canonical_source_sha256") != source.get("canonical_body_sha256"):
        errors.append("recovery canonical source hash differs")
    if scope.get("page_ids") != page_ids or scope.get("page_count") != len(page_ids):
        errors.append("recovery page count or order differs")
    titles = [str(page.get("primary_work_title") or page.get("title") or "") for page in pages]
    footers: list[str] = []
    for page in pages:
        if not isinstance(page, dict):
            continue
        prompt_path, asset_path, prompt_hash = locked_infographic_context(package, page)
        if not prompt_path.is_file() or sha256_text(prompt_path.read_text(encoding="utf-8").rstrip("\n")) != prompt_hash:
            errors.append("locked prompt binding changed for " + str(page.get("page_id") or ""))
            continue
        visible = visible_text_from_prompt(prompt_path.read_text(encoding="utf-8").rstrip("\n"))
        if not visible:
            errors.append("locked footer is unavailable for " + str(page.get("page_id") or ""))
        else:
            footers.append(visible[-1])
        record = (ledger.get("pages") or {}).get(str(page.get("page_id") or ""))
        attempts = record.get("attempts") if isinstance(record, dict) else None
        if (not isinstance(record, dict) or record.get("locked_prompt_sha256") != prompt_hash
                or record.get("asset_path") != asset_path.relative_to(package).as_posix()
                or not isinstance(attempts, list) or not attempts):
            errors.append("attempt binding changed for " + str(page.get("page_id") or ""))
    if scope.get("shared_titles") != titles:
        errors.append("recovery shared title scope differs")
    if scope.get("shared_footers") != footers:
        errors.append("recovery shared footer scope differs")
    return errors


def _prepare_incomplete_page_recovery(
    root: Path,
    original_kp_path: Path,
    original_kp: dict[str, Any],
    plan: dict[str, Any],
    recovery: dict[str, Any],
    recovery_context: dict[str, str],
    recovery_reason: str,
) -> dict[str, Any]:
    """Re-open only the failed page, keeping accepted siblings byte-stable in place."""

    package = original_kp_path.parent
    job = validate_job_package_binding(root, original_kp_path, original_kp)
    action_path = image_action_envelope_path(package)
    if action_path.is_file() and read_json(action_path).get("state") != "completed":
        raise CompilerWorkerV2Error("incomplete recovery cannot run while an image action is pending")
    source = original_kp.get("source") if isinstance(original_kp.get("source"), dict) else {}
    ledger_path = image_attempt_ledger_path(package)
    ledger = load_image_attempt_ledger(package, str(source.get("source_identity") or ""))
    errors = _incomplete_recovery_scope_errors(root, package, original_kp, job, ledger, recovery)
    if errors:
        raise CompilerWorkerV2Error("page-scoped recovery refused: " + "; ".join(errors))
    failed_page_id = recovery_context["failed_page_id"]
    pages = original_kp["infographics"]
    failed_page = infographic_page(original_kp, failed_page_id)
    for page in pages:
        page_id = str(page.get("page_id") or "")
        record = ledger["pages"].get(page_id)
        latest = (record or {}).get("attempts", [])[-1] if isinstance(record, dict) and (record.get("attempts") or []) else None
        if page_id == failed_page_id:
            continue
        if page.get("status") != "receipt_verified" or not isinstance(latest, dict) or latest.get("status") != "accepted":
            raise CompilerWorkerV2Error("page-scoped recovery requires every sibling page to remain accepted")
        asset = package_relative_path(package, str(page.get("asset_path") or ""), "accepted sibling asset")
        if not asset.is_file() or latest.get("png_sha256") != sha256_bytes(asset.read_bytes()):
            raise CompilerWorkerV2Error("accepted sibling PNG binding changed")
    if "source_anchors" in recovery:
        raise CompilerWorkerV2Error("page-scoped recovery cannot change locked source_anchors or copy")
    clear_infographic_receipt(failed_page)
    failed_page["status"] = "planned"
    original_kp["incomplete_recovery"] = {
        "failed_page_id": failed_page_id,
        "reason": recovery_reason,
        "prepared_at": iso_now(),
        "recovery_context": recovery_context,
        "revision_plan_payload_sha256": canonical_payload_sha256(plan),
    }
    write_json(original_kp_path, original_kp)
    job.update({"status": "running", "updated_at": iso_now()})
    write_v2_job(root, job)
    return {
        "status": "prepared",
        "recovery_mode": "page_scoped",
        "page_id": failed_page_id,
        "knowledge_package": original_kp_path.relative_to(root).as_posix(),
        "preserved_page_ids": [str(page.get("page_id") or "") for page in pages if page is not failed_page],
        "write_performed": True,
        "next_action": "compile only the recovered page; accepted sibling PNGs and attempts remain locked",
    }


def command_prepare_infographic_revision(args: argparse.Namespace) -> int:
    root = args.root.resolve()
    original_kp_path = relative_to_root(
        args.kp_file,
        root,
        "original Knowledge Package",
    )
    plan_path = relative_to_root(args.plan_file, root, "infographic revision plan")
    if not original_kp_path.is_file() or not plan_path.is_file():
        raise CompilerWorkerV2Error(
            "original Knowledge Package or infographic revision plan is missing"
        )
    original_package = original_kp_path.parent
    original_completion_path = original_package / "completion_receipt.json"
    original_kp = read_json(original_kp_path)
    plan = read_json(plan_path)
    if plan.get("schema_version") != INFOGRAPHIC_REVISION_PLAN_VERSION:
        raise CompilerWorkerV2Error("unsupported infographic revision plan")
    if plan.get("mode") == "same_job_text_revision":
        if args.recover_incomplete:
            raise CompilerWorkerV2Error("text revision cannot reset an incomplete job")
        result = text_revision.prepare(sys.modules[__name__], args, plan)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    incomplete_recovery = bool(args.recover_incomplete)
    recovery_context: dict[str, str] = {}
    recovery_reason = ""
    if incomplete_recovery:
        if original_completion_path.exists():
            raise CompilerWorkerV2Error(
                "incomplete infographic recovery cannot replace a completed package"
            )
        recovery = plan.get("recovery") if isinstance(plan.get("recovery"), dict) else {}
        failed_page_id = str(recovery.get("failed_page_id") or "")
        recovery_reason = str(recovery.get("reason") or "")
        if recovery_reason not in {"exhausted_image_text_contract", "exhausted_visual_contract"} or not failed_page_id:
            raise CompilerWorkerV2Error(
                "incomplete infographic recovery requires its supported exhausted page"
            )
        source_for_recovery = (
            original_kp.get("source")
            if isinstance(original_kp.get("source"), dict)
            else {}
        )
        if recovery_reason == "exhausted_visual_contract":
            recovery_context = visual_recovery_context(
                root=root, original_kp_path=original_kp_path, original_kp=original_kp,
                original_package=original_package, recovery=recovery,
            )
        else:
            ledger_path = image_attempt_ledger_path(original_package)
            if not ledger_path.is_file():
                raise CompilerWorkerV2Error("incomplete infographic recovery requires an image attempt ledger")
            ledger = load_image_attempt_ledger(original_package, str(source_for_recovery.get("source_identity") or ""))
            record = ledger["pages"].get(failed_page_id)
            attempts = record.get("attempts") if isinstance(record, dict) else None
            latest = attempts[-1] if isinstance(attempts, list) and attempts else None
            if (not isinstance(latest, dict) or len(attempts) != int(IMAGEGEN_GENERATION_POLICY["max_candidates_per_asset"])
                    or latest.get("status") != "blocking_failure" or latest.get("failure_code") != "wrong_or_missing_locked_text"):
                raise CompilerWorkerV2Error("incomplete infographic recovery requires an exhausted locked-text failure")
            recovery_context = {
                "failed_page_id": failed_page_id,
                "failure_code": str(latest.get("failure_code") or ""),
                "original_attempt_ledger_path": ledger_path.relative_to(root).as_posix(),
                "original_attempt_ledger_sha256": sha256_bytes(ledger_path.read_bytes()),
            }
        original_completion: dict[str, Any] = {}
    else:
        if not original_completion_path.is_file():
            raise CompilerWorkerV2Error(
                "infographic revision requires an immutable original completion receipt"
            )
        original_completion = read_json(original_completion_path)
        if (
            original_completion.get("schema_version") != COMPLETION_SCHEMA_VERSION
            or original_completion.get("status") != "completed"
            or original_completion.get("payload_sha256")
            != canonical_payload_sha256(original_completion)
            or original_completion.get("knowledge_package_sha256")
            != sha256_bytes(original_kp_path.read_bytes())
        ):
            raise CompilerWorkerV2Error(
                "original completion receipt does not match its Knowledge Package"
            )
    revision_id = validated_identifier(
        str(plan.get("revision_id") or ""),
        "revision_id",
    )
    if str(args.revision_id or "") and args.revision_id != revision_id:
        raise CompilerWorkerV2Error(
            "revision id does not match the infographic revision plan"
        )
    source = (
        original_kp.get("source")
        if isinstance(original_kp.get("source"), dict)
        else {}
    )
    if (
        plan.get("source_id") != source.get("source_id")
        or plan.get("source_identity") != source.get("source_identity")
    ):
        raise CompilerWorkerV2Error(
            "infographic revision plan does not match the original source"
        )
    if incomplete_recovery:
        if plan.get("source_updates") not in (None, {}):
            raise CompilerWorkerV2Error("page-scoped recovery cannot change shared source metadata")
        recovery = plan["recovery"]
        assert isinstance(recovery, dict)
        result = _prepare_incomplete_page_recovery(
            root, original_kp_path, original_kp, plan, recovery,
            recovery_context, recovery_reason,
        )
        print(json.dumps(result, ensure_ascii=False))
        return 0
    coverage_units = plan.get("coverage_units")
    infographics = plan.get("infographics")
    if not isinstance(coverage_units, list) or not coverage_units:
        raise CompilerWorkerV2Error("infographic revision plan lacks coverage_units")
    if not isinstance(infographics, list) or not infographics:
        raise CompilerWorkerV2Error("infographic revision plan lacks infographics")
    if any(
        not isinstance(item, dict) or item.get("status") != "planned"
        for item in infographics
    ):
        raise CompilerWorkerV2Error(
            "infographic revision pages must begin in planned status"
        )
    source_updates = plan.get("source_updates") or {}
    if not isinstance(source_updates, dict):
        raise CompilerWorkerV2Error("infographic revision source_updates is invalid")
    allowed_source_updates = {
        "source_label",
        "original_date",
        "primary_work_status",
        "primary_work_title",
        "primary_work_evidence",
    }
    if set(source_updates) - allowed_source_updates:
        raise CompilerWorkerV2Error(
            "infographic revision source_updates contains unsupported fields"
        )
    job_id = validated_identifier(
        str(plan.get("job_id") or f"CW2-{revision_id}"),
        "job_id",
    )

    target = relative_to_root(
        original_package / "revisions" / revision_id,
        root,
        "infographic revision package",
    )
    plan_sha256 = sha256_bytes(plan_path.read_bytes())
    origin_receipt = {
        "schema_version": INFOGRAPHIC_REVISION_PLAN_VERSION,
        "revision_id": revision_id,
        "source_id": source.get("source_id"),
        "source_identity": source.get("source_identity"),
        "original_package": original_package.relative_to(root).as_posix(),
        "origin_status": "completed_revision",
        "original_knowledge_package_sha256": sha256_bytes(original_kp_path.read_bytes()),
        "original_completion_path": original_completion_path.relative_to(root).as_posix(),
        "original_completion_payload_sha256": original_completion.get("payload_sha256"),
        "revision_plan_path": plan_path.relative_to(root).as_posix(),
        "revision_plan_sha256": plan_sha256,
    }
    origin_receipt["payload_sha256"] = canonical_payload_sha256(origin_receipt)
    origin_path = target / "revision_origin.json"
    revision_kp_path = target / "knowledge_package.json"
    if target.exists():
        if (
            origin_path.is_file()
            and revision_kp_path.is_file()
            and read_json(origin_path) == origin_receipt
        ):
            print(json.dumps({
                "status": "prepared",
                "revision_id": revision_id,
                "package_dir": target.relative_to(root).as_posix(),
                "knowledge_package": revision_kp_path.relative_to(root).as_posix(),
                "write_performed": False,
            }, ensure_ascii=False))
            return 0
        raise CompilerWorkerV2Error(
            "infographic revision package already exists with different content"
        )
    if job_file(root, job_id).exists():
        raise CompilerWorkerV2Error("infographic revision job_id already exists")

    target.mkdir(parents=True)
    for filename in (
        "canonical_source.md",
        "source_capture.md",
        "source_capture_metadata.json",
        "review_packet.json",
        "image_analysis.json",
    ):
        source_path = original_package / filename
        if source_path.is_file():
            shutil.copy2(source_path, target / filename)
    metadata_path = original_package / "source_capture_metadata.json"
    if metadata_path.is_file():
        original_image_value = str(
            read_json(metadata_path).get("original_image_path") or ""
        )
        if original_image_value:
            original_image = package_relative_path(
                original_package,
                original_image_value,
                "original image",
            )
            image_target = target / original_image_value
            image_target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(original_image, image_target)

    revision_kp = json.loads(json.dumps(original_kp, ensure_ascii=False))
    revision_kp["job_id"] = job_id
    revision_kp["coverage_units"] = coverage_units
    revision_kp["infographics"] = infographics
    revision_source = revision_kp.get("source")
    if not isinstance(revision_source, dict):
        raise CompilerWorkerV2Error("original Knowledge Package source is invalid")
    revision_source.update(source_updates)
    original_slug = (
        original_package.name.split("_", 1)[1]
        if "_" in original_package.name
        else original_package.name.casefold()
    )
    revision_kp["revision"] = {
        "kind": "infographic_revision",
        "revision_id": revision_id,
        "source_slug": original_slug,
        "asset_slug": bounded_slug(f"{original_slug}_{revision_id}"),
        "origin_package": original_package.relative_to(root).as_posix(),
        "revision_plan_sha256": plan_sha256,
    }
    revision_kp["revision"].update({
        "supersedes_completion_path": original_completion_path.relative_to(root).as_posix(),
        "supersedes_completion_payload_sha256": original_completion.get("payload_sha256"),
    })
    write_json(revision_kp_path, revision_kp)
    write_json(origin_path, origin_receipt)
    now = iso_now()
    write_v2_job(root, {
        "schema_version": JOB_SCHEMA_VERSION,
        "job_id": job_id,
        "job_kind": "infographic_revision",
        "revision_id": revision_id,
        "source_id": source.get("source_id"),
        "source_input_kind": source.get("source_input_kind"),
        "source_identity": source.get("source_identity"),
        "status": "running",
        "first_accepted_at": now,
        "updated_at": now,
        "worker_runtime": "direct",
        "package_dir": target.relative_to(root).as_posix(),
        "supersedes_completion_path": original_completion_path.relative_to(root).as_posix(),
    })
    print(json.dumps({
        "status": "prepared",
        "revision_id": revision_id,
        "package_dir": target.relative_to(root).as_posix(),
        "knowledge_package": revision_kp_path.relative_to(root).as_posix(),
        "write_performed": True,
        "next_action": "run compile-infographics for the revision Knowledge Package",
    }, ensure_ascii=False))
    return 0


def read_case_revalidation_origin_manifest(
    package: Path, source: dict[str, Any],
) -> dict[str, Any]:
    """Read historical identity after completed_receipt_readback, not new acceptance.

    A rejected legacy case may lack evidence now required for a new result.
    Its receipt-bound manifest must remain immutable; the replacement manifest
    is validated separately against today's complete selection contract.
    """
    raw = str(source.get("case_evidence_manifest_path") or "")
    expected = str(source.get("case_evidence_manifest_sha256") or "")
    if not raw or not expected:
        raise CompilerWorkerV2Error("case revalidation origin manifest binding is missing")
    path = relative_to_root(package / raw, package, "case revalidation origin manifest")
    if not path.is_file() or sha256_bytes(path.read_bytes()) != expected:
        raise CompilerWorkerV2Error("case revalidation origin manifest drifted")
    manifest = read_json(path)
    if (
        manifest.get("schema_version") not in {CASE_MANIFEST_VERSION, CASE_MANIFEST_VERSION_V2}
        or manifest.get("asset_mode") != ASSET_MODE_CASE
        or not source.get("case_evidence_source_manifest_sha256")
        or manifest.get("source_manifest_sha256") != source.get("case_evidence_source_manifest_sha256")
        or manifest.get("primary_source_url") != (source.get("source_url") or source.get("normalized_url"))
        or not str(manifest.get("case_id") or "").strip()
    ):
        raise CompilerWorkerV2Error("case revalidation origin identity binding is invalid")
    return manifest


def command_prepare_case_revalidation(args: argparse.Namespace) -> int:
    """Prepare an immutable, source-bound revision for one completed case.

    This deliberately creates a new Compiler job and package.  The accepted
    source package, its receipt and its capacity record never move or change.
    The normal ingest rail can then project the revised package through the
    one Vault writer and produce a second, independently hash-bound receipt.
    """
    root = args.root.resolve()
    original_kp_path = relative_to_root(args.kp_file, root, "original Knowledge Package")
    plan_path = relative_to_root(args.plan_file, root, "case revalidation plan")
    if not original_kp_path.is_file() or not plan_path.is_file():
        raise CompilerWorkerV2Error("original Knowledge Package or case revalidation plan is missing")
    original_package = original_kp_path.parent
    original_completion_path = original_package / "completion_receipt.json"
    if not original_completion_path.is_file():
        raise CompilerWorkerV2Error("case revalidation requires an immutable original completion receipt")
    original_kp = read_json(original_kp_path)
    original_source = original_kp.get("source") if isinstance(original_kp.get("source"), dict) else {}
    if original_source.get("asset_mode") != ASSET_MODE_CASE:
        raise CompilerWorkerV2Error("case revalidation requires a completed case-evidence package")
    original_completion = completed_receipt_readback(
        root, original_package, original_kp_path, original_kp, allow_derived_outcome_drift=True,
    )
    if not original_completion:
        raise CompilerWorkerV2Error("case revalidation requires a completed original package")
    plan = read_json(plan_path)
    if plan.get("schema_version") != CASE_REVALIDATION_PLAN_VERSION:
        raise CompilerWorkerV2Error("unsupported case revalidation plan")
    revision_id = validated_identifier(str(plan.get("revision_id") or ""), "revision_id")
    if str(args.revision_id or "") and str(args.revision_id) != revision_id:
        raise CompilerWorkerV2Error("revision id does not match the case revalidation plan")
    job_id = validated_identifier(str(plan.get("job_id") or f"CW2-{revision_id}"), "job_id")
    if (
        plan.get("source_id") != original_source.get("source_id")
        or plan.get("source_identity") != original_source.get("source_identity")
    ):
        raise CompilerWorkerV2Error("case revalidation plan does not match the original source")
    original_case = original_kp.get("case_evidence") if isinstance(original_kp.get("case_evidence"), dict) else {}
    original_manifest = read_case_revalidation_origin_manifest(original_package, original_source)
    original_case_id = str(original_case.get("case_id") or original_manifest.get("case_id") or "")
    if original_case.get("case_id") and original_case.get("case_id") != original_manifest.get("case_id"):
        raise CompilerWorkerV2Error("case revalidation original package and manifest case IDs disagree")
    if plan.get("case_id") != original_case_id:
        raise CompilerWorkerV2Error("case revalidation plan does not match the original case")
    original_receipt_hash = sha256_bytes(original_completion_path.read_bytes())
    if plan.get("original_completion_receipt_sha256") != original_receipt_hash:
        raise CompilerWorkerV2Error("case revalidation plan does not bind the immutable original receipt")
    canonical = (original_package / "canonical_source.md")
    if not canonical.is_file():
        raise CompilerWorkerV2Error("case revalidation original canonical source is missing")
    canonical_hash = sha256_text(canonical.read_text(encoding="utf-8").rstrip("\n"))
    if canonical_hash != original_source.get("canonical_body_sha256"):
        raise CompilerWorkerV2Error("case revalidation original canonical source hash mismatch")
    if plan.get("canonical_source_sha256") != canonical_hash:
        raise CompilerWorkerV2Error("case revalidation plan does not bind the canonical source")
    raw_contract = plan.get("selection_contract")
    try:
        selection_contract = validate_selection_contract(raw_contract)
    except CaseEvidenceError as exc:
        raise CompilerWorkerV2Error(f"case revalidation selection contract is invalid: {exc}") from exc
    selection_contract_hash = canonical_payload_sha256(selection_contract)
    if plan.get("selection_contract_sha256") != selection_contract_hash:
        raise CompilerWorkerV2Error("case revalidation plan selection contract hash mismatch")
    raw_evidence = str(plan.get("case_evidence_file") or "")
    if not raw_evidence:
        raise CompilerWorkerV2Error("case revalidation plan requires case_evidence_file")
    evidence_path = relative_to_root(Path(raw_evidence), root, "case revalidation evidence manifest")
    try:
        manifest, _ = validate_case_evidence_manifest(
            root,
            evidence_path,
            primary_url=str(original_source.get("source_url") or ""),
            selection_contract=selection_contract,
        )
    except CaseEvidenceError as exc:
        raise CompilerWorkerV2Error(f"case revalidation evidence is invalid: {exc}") from exc
    if manifest.get("case_id") != plan.get("case_id") or manifest.get("topic_id") != plan.get("topic_id"):
        raise CompilerWorkerV2Error("case revalidation evidence identity does not match the plan")
    application_update = plan.get("application_update")
    allowed_application_fields = {"adoption_status", "adoption_reason", "compounding_next_action"}
    if (
        not isinstance(application_update, dict)
        or set(application_update) != allowed_application_fields
        or any(not isinstance(application_update.get(key), str) or not application_update[key].strip() for key in allowed_application_fields)
    ):
        raise CompilerWorkerV2Error("case revalidation plan requires the complete bounded application_update")
    if application_update["adoption_status"] not in VALID_ADOPTION_STATUSES:
        raise CompilerWorkerV2Error(
            "case revalidation application adoption_status must be a supported application state; "
            "method verification is recorded separately"
        )

    target = relative_to_root(
        original_package / "revisions" / revision_id,
        root,
        "case revalidation package",
    )
    plan_hash = sha256_bytes(plan_path.read_bytes())
    origin = {
        "schema_version": CASE_REVALIDATION_ORIGIN_VERSION,
        "revision_id": revision_id,
        "job_id": job_id,
        "case_id": plan["case_id"],
        "topic_id": plan["topic_id"],
        "source_id": original_source.get("source_id"),
        "source_identity": original_source.get("source_identity"),
        "original_package": original_package.relative_to(root).as_posix(),
        "original_knowledge_package_sha256": sha256_bytes(original_kp_path.read_bytes()),
        "original_completion_path": original_completion_path.relative_to(root).as_posix(),
        "original_completion_receipt_sha256": original_receipt_hash,
        "canonical_source_sha256": canonical_hash,
        "selection_contract": selection_contract,
        "selection_contract_sha256": selection_contract_hash,
        "revision_plan_sha256": plan_hash,
    }
    origin["payload_sha256"] = canonical_payload_sha256(origin)
    origin_path = target / "revision_origin.json"
    target_plan_path = target / "revalidation_plan.json"
    revision_kp_path = target / "knowledge_package.json"
    if target.exists():
        if not (origin_path.is_file() and target_plan_path.is_file() and revision_kp_path.is_file()):
            raise CompilerWorkerV2Error("case revalidation package is incomplete; inspect before recovery")
        stored_origin = read_json(origin_path)
        has_job = job_file(root, job_id).exists()
        has_receipt = (target / "completion_receipt.json").is_file()
        if sha256_bytes(target_plan_path.read_bytes()) != plan_hash:
            # Earlier builds re-serialized this plan, changing its byte hash
            # despite identical content.  Only a pre-job/pre-receipt package
            # with equal parsed content may be restored to the exact input
            # bytes.  Once a job or receipt exists, the stored byte binding is
            # immutable and any difference remains a hard stop.
            if has_job or has_receipt or read_json(target_plan_path) != plan:
                raise CompilerWorkerV2Error("case revalidation package already exists with different input")
        if stored_origin != origin:
            stored_input = dict(stored_origin)
            expected_input = dict(origin)
            for field in ("selection_contract", "selection_contract_sha256", "payload_sha256"):
                stored_input.pop(field, None)
                expected_input.pop(field, None)
            if has_job or has_receipt or stored_input != expected_input:
                raise CompilerWorkerV2Error("case revalidation package already exists with different input")
            # A pre-job recovery can rederive only the normalized contract
            # binding after a validator repair.  The raw plan byte hash and
            # every source/receipt input above remain unchanged.
        if has_job:
            revision_kp = read_json(revision_kp_path)
            validate_job_package_binding(root, revision_kp_path, revision_kp)
            try:
                read_case_evidence_manifest(target, revision_kp["source"], selection_contract=selection_contract)
            except CaseEvidenceError as exc:
                raise CompilerWorkerV2Error(f"case revalidation staged evidence drifted: {exc}") from exc
            print(json.dumps({
                "status": "prepared",
                "revision_id": revision_id,
                "package_dir": target.relative_to(root).as_posix(),
                "knowledge_package": revision_kp_path.relative_to(root).as_posix(),
                "write_performed": False,
                "next_action": "run ingest_rail.py advance for this exact revision job",
            }, ensure_ascii=False))
            return 0
        if has_receipt:
            raise CompilerWorkerV2Error("case revalidation package has a completion receipt but its job is missing")
        # An interrupted build may have written only deterministic local files.
        # The exact origin and plan hashes above let us rebuild that same
        # revision in place without freeing a slot or replacing an accepted
        # case.  A writer job or receipt always takes the immutable branch.
    if job_file(root, job_id).exists():
        raise CompilerWorkerV2Error("case revalidation job_id already exists")

    target.mkdir(parents=True, exist_ok=True)
    mirror_revision_input_evidence(original_package, target, job_id=job_id, source=original_source)
    try:
        binding = stage_case_evidence_manifest(
            root,
            evidence_path,
            target,
            primary_url=str(original_source.get("source_url") or ""),
        )
    except CaseEvidenceError as exc:
        raise CompilerWorkerV2Error(f"case revalidation evidence cannot be staged: {exc}") from exc
    staged_source = {**original_source, **binding}
    try:
        staged_manifest = read_case_evidence_manifest(
            target,
            staged_source,
            selection_contract=selection_contract,
        )
    except CaseEvidenceError as exc:
        raise CompilerWorkerV2Error(f"case revalidation staged evidence drifted: {exc}") from exc
    # ``input_bundle.json`` is the plan builder's accepted-input authority.
    # Keep it in the same staged-manifest transaction as the package source;
    # otherwise a later pre-writer repair sees the old case manifest while the
    # revision KP correctly points at the new one.
    bundle_path = target / "input_bundle.json"
    bundle = read_json(bundle_path)
    bundle.update(binding)
    write_json(bundle_path, bundle)
    review_path = target / "review_packet.json"
    if review_path.is_file():
        review = read_json(review_path)
        review.update(binding)
        write_json(review_path, review)
    revision_kp = json.loads(json.dumps(original_kp, ensure_ascii=False))
    revision_kp["job_id"] = job_id
    revision_source = revision_kp.get("source")
    if not isinstance(revision_source, dict):
        raise CompilerWorkerV2Error("case revalidation original source is invalid")
    revision_source.update(binding)
    evidence = revision_kp.get("case_evidence")
    if not isinstance(evidence, dict):
        raise CompilerWorkerV2Error("case revalidation original case evidence is invalid")
    evidence.update({
        "case_id": manifest["case_id"],
        "manifest_path": binding["case_evidence_manifest_path"],
        "manifest_sha256": binding["case_evidence_manifest_sha256"],
        "creator": staged_manifest["creator"],
        "screenshots": staged_manifest["screenshots"],
        "technical_references": staged_manifest["technical_references"],
        "reproduction": staged_manifest["reproduction"],
        "method_card": staged_manifest["method_card"],
    })
    application = revision_kp.get("application")
    if not isinstance(application, dict):
        raise CompilerWorkerV2Error("case revalidation original application is invalid")
    application.update(application_update)
    original_slug = original_package.name.split("_", 1)[1] if "_" in original_package.name else original_package.name.casefold()
    revision_kp["revision"] = {
        "kind": "case_revalidation",
        "revision_id": revision_id,
        "source_slug": original_slug,
        "asset_slug": bounded_slug(f"{original_slug}_{revision_id}"),
        "origin_package": original_package.relative_to(root).as_posix(),
        "original_completion_path": original_completion_path.relative_to(root).as_posix(),
        "original_completion_receipt_sha256": original_receipt_hash,
        "selection_contract": selection_contract,
        "selection_contract_sha256": selection_contract_hash,
        "revision_plan_sha256": plan_hash,
    }
    target_plan_path.write_bytes(plan_path.read_bytes())
    write_json(origin_path, origin)
    write_json(revision_kp_path, revision_kp)
    try:
        read_case_evidence_manifest(target, revision_source, selection_contract=selection_contract)
    except CaseEvidenceError as exc:
        raise CompilerWorkerV2Error(f"case revalidation staged evidence drifted: {exc}") from exc
    now = iso_now()
    job = {
        "schema_version": JOB_SCHEMA_VERSION,
        "job_id": job_id,
        "job_kind": "case_revalidation",
        "revision_id": revision_id,
        "source_id": revision_source.get("source_id"),
        "source_input_kind": revision_source.get("source_input_kind"),
        "source_identity": revision_source.get("source_identity"),
        "source_profile": revision_source.get("source_profile", "article"),
        "asset_mode": ASSET_MODE_CASE,
        "case_evidence_manifest_path": (target / binding["case_evidence_manifest_path"]).relative_to(root).as_posix(),
        "status": "running",
        "first_accepted_at": now,
        "updated_at": now,
        "worker_runtime": "direct",
        "package_dir": target.relative_to(root).as_posix(),
        "rail_events": [],
        "revision_origin_path": origin_path.relative_to(root).as_posix(),
    }
    scope = case_execution_scope(job, revision_kp, target)
    job["execution_contract"] = {
        "schema_version": EXECUTION_CONTRACT_VERSION,
        "mode": "case_evidence_zero_imagegen",
        "scope": scope,
        "scope_sha256": canonical_payload_sha256(scope),
        "first_accepted_at": now,
        "imagegen": "prohibited",
    }
    write_v2_job(root, job)
    print(json.dumps({
        "status": "prepared",
        "revision_id": revision_id,
        "job_id": job_id,
        "package_dir": target.relative_to(root).as_posix(),
        "knowledge_package": revision_kp_path.relative_to(root).as_posix(),
        "write_performed": True,
        "next_action": "run ingest_rail.py advance for this exact revision job",
    }, ensure_ascii=False))
    return 0


_YOUTUBE_TRANSCRIPT_HEADER = "# YouTube transcript"
_YOUTUBE_TRANSCRIPT_REQUIRED_METADATA = ("Video URL: https://www.youtube.com/watch?v=", "Video ID: ", "Caption language: ", "Caption track: ")


def is_youtube_caption_transcript(canonical_body: str) -> bool:
    """Recognise the accepted VTT-derived body without treating cues as article headings."""
    lines = [line.strip() for line in canonical_body.splitlines() if line.strip()]
    return (
        len(lines) > len(_YOUTUBE_TRANSCRIPT_REQUIRED_METADATA)
        and lines[0] == _YOUTUBE_TRANSCRIPT_HEADER
        and all(lines[index + 1].startswith(prefix) for index, prefix in enumerate(_YOUTUBE_TRANSCRIPT_REQUIRED_METADATA))
        and any(re.match(r"^\[[^\]]+\]\s+\S", line) for line in lines[1 + len(_YOUTUBE_TRANSCRIPT_REQUIRED_METADATA):])
    )


def comparable_unit_text(value: str) -> str:
    return re.sub(r"\s+", "", value).casefold()


def line_looks_like_required_child_unit(line: str, next_line: str) -> bool:
    if not line or not next_line:
        return False
    if len(line) > 64:
        return False
    if line.endswith(("。", "，", "、", "；", "：", ":", ".", "?", "？", "！", "!")):
        return False
    if line.startswith(("「", "」", "請", "為此", "哪些", "這個", "總結", "找出所有", "將這段")):
        return False
    return any(marker in line for marker in ACTION_UNIT_MARKERS)


def extracted_required_units(canonical_body: str) -> list[dict[str, str]]:
    if is_youtube_caption_transcript(canonical_body):
        return []
    lines = [line.strip() for line in canonical_body.splitlines() if line.strip()]
    units: list[dict[str, str]] = []
    active_required_section = ""
    for index, line in enumerate(lines):
        if line in SECTION_HEADINGS_WITH_REQUIRED_CHILDREN:
            active_required_section = line
            continue
        if re.match(r"^使用案例\s*[0-9一二三四五六七八九十]+[：:]", line):
            units.append({"text": line, "parent": "使用案例", "kind": "structure_unit"})
            active_required_section = ""
            continue
        if re.match(r"^第\s*[0-9一二三四五六七八九十]+\s*階[，,：:、 ]", line):
            units.append({"text": line, "parent": "階段框架", "kind": "structure_unit"})
            active_required_section = ""
            continue
        if re.match(r"^(?:Then|Now)\s*:\s*\S", line, flags=re.IGNORECASE):
            units.append({"text": line, "parent": "paired_transition", "kind": "comparison_unit"})
            active_required_section = ""
            continue
        if re.match(
            r"^(?:第\s*[0-9一二三四五六七八九十]+\s*步|判斷標準)\s*[：:]",
            line,
        ):
            units.append({"text": line, "parent": "step_sequence", "kind": "action_unit"})
            active_required_section = ""
            continue
        next_line = lines[index + 1] if index + 1 < len(lines) else ""
        if active_required_section and line_looks_like_required_child_unit(line, next_line):
            units.append({"text": line, "parent": active_required_section, "kind": "action_unit"})
    deduped: list[dict[str, str]] = []
    seen: set[str] = set()
    for unit in units:
        key = comparable_unit_text(unit["text"])
        if key not in seen:
            deduped.append(unit)
            seen.add(key)
    return deduped


def plain_structural_heading(line: str, next_line: str, *, first: bool) -> bool:
    if first or not line or not next_line or len(line) > 72:
        return False
    if line.startswith(("#", "-", "*", "|", "•", "http://", "https://")):
        return False
    if line.endswith(("。", "，", "；", "：", ":", "?", "？", "！", "!", ".")):
        return False
    if re.fullmatch(r"[\s\d,().%+\-−×]+", line):
        return False
    if re.fullmatch(r"(?:\(?[a-z]\)?|\d+|[\d,]+(?:\.\d+)?|[-−]\s*\d+(?:\.\d+)?)", line):
        return False
    if "(%)" in line or line.endswith("(%)"):
        return False
    if re.search(r"(?:\\|[{}&]|displaystyle|∎|Δ|≤|≥|_\{|\^\{)", line):
        return False
    if re.search(r"(?:et al\.|\([12][0-9]{3}\)|Working Paper|Technical report|Discussion Paper)", line):
        return False
    if re.match(r"^(?:[A-Z]\.\s+){1,}|^[A-Z]\.\s*[A-Za-z].*,", line):
        return False
    if re.match(r"^(?:For|Let|The|These|This)\s+\S", line):
        return False
    if re.match(r"^[a-z]\w*\s+", line):
        return False
    if len(next_line) < 48:
        return False
    if re.match(r"^(?:Then|Now)\s*:", line, flags=re.IGNORECASE):
        return False
    return len(line.split()) <= 10


def extracted_required_sections(canonical_body: str) -> list[dict[str, str]]:
    if is_youtube_caption_transcript(canonical_body):
        return []
    lines = [line.strip() for line in canonical_body.splitlines() if line.strip()]
    heading_indexes: list[tuple[int, str]] = []
    inside_references = False
    for index, line in enumerate(lines):
        if line == "References":
            inside_references = True
            continue
        if inside_references:
            if line.startswith("Appendix "):
                inside_references = False
            else:
                continue
        if line.startswith("Appendix ") or re.match(r"^[A-Z]\.\d+(?:\.\d+)?\s+", line):
            continue
        markdown_heading = re.match(r"^#{3,4}\s+(.+)$", line)
        if markdown_heading:
            heading_indexes.append((index, markdown_heading.group(1).strip()))
            continue
        next_line = lines[index + 1] if index + 1 < len(lines) else ""
        if plain_structural_heading(line, next_line, first=index == 0):
            heading_indexes.append((index, line))
    sections: list[dict[str, str]] = []
    for position, (index, heading) in enumerate(heading_indexes):
        end = heading_indexes[position + 1][0] if position + 1 < len(heading_indexes) else len(lines)
        body = "\n".join(lines[index:end])
        if body:
            sections.append({"text": heading, "body": body})
    return sections


def coverage_unit_dispositions(unit: dict[str, Any]) -> set[str]:
    raw = unit.get("dispositions", unit.get("disposition", []))
    if isinstance(raw, str):
        return {raw}
    if isinstance(raw, list):
        return {str(value) for value in raw if str(value).strip()}
    return set()


def build_coverage_audit(kp: dict[str, Any]) -> dict[str, Any]:
    coverage_units = [
        unit
        for unit in kp.get("coverage_units") or []
        if isinstance(unit, dict) and str(unit.get("id") or "").strip()
    ]
    infographics = [
        page for page in kp.get("infographics") or [] if isinstance(page, dict)
    ]
    unit_ids = [str(unit.get("id") or "").strip() for unit in coverage_units]
    unit_id_set = set(unit_ids)
    infographic_unit_ids = {
        str(unit.get("id") or "").strip()
        for unit in coverage_units
        if "infographic" in coverage_unit_dispositions(unit)
    }
    mapped_counts: dict[str, int] = {}
    page_rows: list[dict[str, Any]] = []
    for page_index, page in enumerate(infographics, start=1):
        page_ids: list[str] = []
        for mapping in page.get("content_map") or []:
            if not isinstance(mapping, dict):
                continue
            coverage_id = str(mapping.get("coverage_id") or "").strip()
            if not coverage_id:
                continue
            mapped_counts[coverage_id] = mapped_counts.get(coverage_id, 0) + 1
            page_ids.append(coverage_id)
        if not page_ids:
            page_ids = string_list(page.get("coverage_ids"))
        page_rows.append({
            "page": page_index,
            "page_id": str(page.get("page_id") or ""),
            "title": str(page.get("title") or ""),
            "coverage_ids": page_ids,
            "visible_text_count": len(string_list(page.get("visible_text"))),
        })

    mapped_ids = set(mapped_counts)
    disposition_counts = {
        name: sum(
            1
            for unit in coverage_units
            if name in coverage_unit_dispositions(unit)
        )
        for name in sorted(VALID_COVERAGE_DISPOSITIONS)
    }
    summary_only_ids = [
        unit_id
        for unit_id, unit in zip(unit_ids, coverage_units)
        if coverage_unit_dispositions(unit) == {"summary"}
    ]
    omitted = [
        {
            "id": str(unit.get("id") or ""),
            "reason": str(unit.get("omission_reason") or ""),
        }
        for unit in coverage_units
        if "omitted" in coverage_unit_dispositions(unit)
    ]
    missing_mappings = sorted(infographic_unit_ids - mapped_ids)
    duplicate_mappings = sorted(
        coverage_id for coverage_id, count in mapped_counts.items() if count > 1
    )
    unknown_mappings = sorted(mapped_ids - unit_id_set)
    page_count = len(infographics)
    if page_count > 8:
        status = "blocked"
    elif missing_mappings or duplicate_mappings or unknown_mappings:
        status = "needs_review"
    else:
        status = "complete"
    multi_volume_status = (
        "single_page" if page_count <= 1
        else "standard_multi_page" if page_count <= 5
        else "multi_volume_source" if page_count <= 8
        else "batch_limit_exceeded"
    )
    return {
        "schema_version": COVERAGE_AUDIT_SCHEMA_VERSION,
        "status": status,
        "total_units": len(coverage_units),
        "mapped_to_infographic": len(infographic_unit_ids & mapped_ids),
        "missing_infographic_mappings": missing_mappings,
        "duplicate_infographic_mappings": duplicate_mappings,
        "unknown_infographic_mappings": unknown_mappings,
        "summary_only_units": summary_only_ids,
        "omitted_units": omitted,
        "disposition_counts": disposition_counts,
        "infographic_page_count": page_count,
        "multi_volume_status": multi_volume_status,
        "pages": page_rows,
    }


def validate_coverage_audit(kp: dict[str, Any]) -> list[str]:
    audit = build_coverage_audit(kp)
    errors: list[str] = []
    if int(audit["infographic_page_count"]) > 8:
        errors.append("infographic page count exceeds the single-batch safety limit")
    for coverage_id in audit["missing_infographic_mappings"]:
        errors.append(f"coverage unit {coverage_id} has no infographic page mapping")
    for coverage_id in audit["duplicate_infographic_mappings"]:
        errors.append(f"coverage unit {coverage_id} is mapped to multiple infographic pages")
    for coverage_id in audit["unknown_infographic_mappings"]:
        errors.append(f"infographic content map references unknown coverage unit {coverage_id}")
    return errors


def validate_coverage_units(kp: dict[str, Any], canonical_body: str) -> list[str]:
    errors: list[str] = []
    coverage_units = kp.get("coverage_units")
    if not isinstance(coverage_units, list) or not coverage_units:
        return ["missing coverage_units ledger"]

    handled: dict[str, dict[str, Any]] = {}
    handled_units: list[dict[str, Any]] = []
    for index, unit in enumerate(coverage_units, start=1):
        if not isinstance(unit, dict):
            errors.append(f"coverage unit {index} is not an object")
            continue
        text = str(unit.get("text") or unit.get("evidence_anchor") or "").strip()
        if not text:
            errors.append(f"coverage unit {index} missing text")
            continue
        dispositions = coverage_unit_dispositions(unit)
        invalid = sorted(dispositions - VALID_COVERAGE_DISPOSITIONS)
        if invalid:
            errors.append(f"coverage unit {index} has invalid disposition: {', '.join(invalid)}")
        if "omitted" in dispositions and not str(unit.get("omission_reason") or "").strip():
            errors.append(f"coverage unit {index} omitted without reason")
        if not dispositions:
            errors.append(f"coverage unit {index} missing disposition")
        if "infographic" in dispositions:
            source_evidence = str(unit.get("source_evidence") or text).strip()
            if not source_evidence or comparable_unit_text(source_evidence) not in comparable_unit_text(canonical_body):
                errors.append(f"infographic coverage unit is not grounded in canonical source: {text}")
        handled[comparable_unit_text(text)] = unit
        handled_units.append(unit)

    for required in extracted_required_units(canonical_body):
        key = comparable_unit_text(required["text"])
        unit = handled.get(key)
        if unit is None:
            unit = next(
                (
                    candidate
                    for candidate in handled_units
                    if key
                    in comparable_unit_text(
                        str(candidate.get("source_evidence") or "")
                    )
                ),
                None,
            )
        if not unit:
            errors.append(f"unhandled required coverage unit: {required['text']}")
            continue
        dispositions = coverage_unit_dispositions(unit)
        if not dispositions & RETAINED_HIGH_VALUE_DISPOSITIONS:
            errors.append(f"required coverage unit only summarized: {required['text']}")
    for section in extracted_required_sections(canonical_body):
        section_body = comparable_unit_text(section["body"])
        unit = next(
            (
                candidate
                for candidate in handled_units
                if comparable_unit_text(
                    str(candidate.get("source_evidence") or "")
                )
                in section_body
                and str(candidate.get("source_evidence") or "").strip()
            ),
            None,
        )
        if unit is None:
            errors.append(
                f"unhandled required coverage section: {section['text']}"
            )
            continue
        if not coverage_unit_dispositions(unit) & RETAINED_HIGH_VALUE_DISPOSITIONS:
            errors.append(
                f"required coverage section only summarized: {section['text']}"
            )
    return errors


def bind_package_to_job(
    root: Path,
    job_id: str,
    source_id: str,
    source_input_kind: str,
    source_identity: str,
    package: Path,
) -> None:
    validated_identifier(source_id, "source_id")
    package = relative_to_root(package, root, "package")
    path = job_file(root, job_id)
    if not path.exists():
        now = iso_now()
        job = {
            "schema_version": JOB_SCHEMA_VERSION,
            "execution_policy_version": EXECUTION_CONTRACT_VERSION,
            "job_id": job_id,
            "source_id": source_id,
            "source_input_kind": source_identity.split(":", 1)[0],
            "source_identity": source_identity,
            "status": "running",
            "first_accepted_at": now,
            "updated_at": now,
            "worker_runtime": "direct",
            "worker_envelope_path": "",
            "dispatch_request_path": "",
            "worker_instance_id": "",
            "worker_dispatch_evidence": "direct input bundle",
        }
    else:
        job = read_json(path)
        if (
            job.get("source_id") != source_id
            or job.get("source_input_kind") != source_input_kind
            or job.get("source_identity") != source_identity
            or (
                job.get("package_dir")
                and str(job.get("package_dir")) != package.relative_to(root).as_posix()
            )
        ):
            raise CompilerWorkerV2Error("input bundle does not match the accepted v2 job")
        if job.get("status") == "completed":
            raise CompilerWorkerV2Error("completed v2 job cannot bind another input bundle")
    job.update({
        "status": "running",
        "updated_at": iso_now(),
        "package_dir": package.relative_to(root).as_posix(),
    })
    job.setdefault("input_ready_at", iso_now())
    bundle = read_json(package / "input_bundle.json")
    job.setdefault("source_profile", bundle.get("source_profile", "article"))
    write_v2_job(root, job)


def existing_input_bundle(root: Path, source_identity: str) -> dict[str, Any] | None:
    job = latest_v2_job_for_source(root, source_identity)
    if not job or not job.get("package_dir"):
        return None
    package = relative_to_root(root / str(job["package_dir"]), root, "existing package")
    bundle_path = package / "input_bundle.json"
    if not bundle_path.is_file():
        return None
    bundle = read_json(bundle_path)
    if bundle.get("status") != "accepted":
        return None
    required_paths = [package / "canonical_source.md", package / "source_capture_metadata.json", package / "review_packet.json"]
    if bundle.get("source_input_kind") == "user_provided_text":
        required_paths.append(package / "source_capture.md")
    elif bundle.get("source_input_kind") == "url" and bundle.get("raw_html_capture_path"):
        if bundle["raw_html_capture_path"] != "source_capture.html":
            raise CompilerWorkerV2Error("existing URL HTML capture path is invalid")
        required_paths.append(package / "source_capture.html")
    elif bundle.get("source_input_kind") == "url" and bundle.get("api_capture_path"):
        if bundle["api_capture_path"] != "source_capture_api.json":
            raise CompilerWorkerV2Error("existing URL API capture path is invalid")
        required_paths.append(package / "source_capture_api.json")
    elif bundle.get("source_input_kind") == "url" and bundle.get("youtube_caption_path"):
        if bundle["youtube_caption_path"] != "source_capture_youtube.vtt" or bundle.get("youtube_info_path") != "source_capture_youtube.info.json":
            raise CompilerWorkerV2Error("existing YouTube caption capture path is invalid")
        required_paths.extend([package / "source_capture_youtube.vtt", package / "source_capture_youtube.info.json"])
    elif bundle.get("source_input_kind") == "user_provided_image":
        original_image_path = str(bundle.get("original_image_path") or "")
        if not original_image_path:
            return None
        original_path = package_relative_path(
            package,
            original_image_path,
            "original image",
        )
        required_paths.extend([original_path, package / "image_analysis.json"])
    if bundle.get("source_identity") != source_identity:
        raise CompilerWorkerV2Error("existing input bundle readback mismatch")
    if any(not path.is_file() for path in required_paths):
        return None
    if bundle.get("source_input_kind") == "user_provided_text":
        source_capture = package / "source_capture.md"
        if sha256_bytes(source_capture.read_bytes()) != bundle.get("source_content_sha256"):
            raise CompilerWorkerV2Error("existing text input bundle hash mismatch")
    elif bundle.get("source_input_kind") == "user_provided_image":
        original_path = package_relative_path(package, str(bundle["original_image_path"]), "original image")
        if sha256_bytes(original_path.read_bytes()) != bundle.get("source_image_sha256"):
            raise CompilerWorkerV2Error("existing image input bundle hash mismatch")
    return {
        "status": job.get("status"),
        "job_id": job.get("job_id"),
        "source_identity": source_identity,
        "package_dir": str(package),
        "duplicate_input": True,
        "write_performed": False,
    }


def write_input_bundle(
    *,
    root: Path,
    source_id: str,
    job_id: str,
    source_input_kind: str,
    source_identity: str,
    title: str,
    title_source: str,
    slug: str,
    canonical_body: str,
    source_fields: dict[str, Any],
    capture_metadata: dict[str, Any],
    review_fields: dict[str, Any],
) -> Path:
    source_id = validated_identifier(source_id, "source_id")
    slug = output_slug(slug)
    now = iso_now()
    capture_date = date.today().isoformat()
    out_dir = package_dir(root, source_id, slug)
    out_dir.mkdir(parents=True, exist_ok=True)
    # `source_capture.md` deliberately keeps the original user bytes, but the
    # canonical body is a cross-platform comparison surface.  Normalise line
    # endings before both hashing and writing it: otherwise a CRLF text input
    # accepted on Windows hashes differently from the LF text returned by
    # `Path.read_text()` during later validation.
    canonical_body = canonical_body.replace("\r\n", "\n").replace("\r", "\n").rstrip("\n")
    if not canonical_body:
        raise CompilerWorkerV2Error("canonical source is empty")
    anchors = [line.strip() for line in canonical_body.splitlines() if line.strip()][:8]
    canonical_hash = sha256_text(canonical_body)
    input_bundle = {
        "schema_version": SCHEMA_VERSION,
        "execution_policy_version": EXECUTION_CONTRACT_VERSION,
        "stage": "input_bundle",
        "status": "accepted",
        "job_id": job_id,
        "source_id": source_id,
        "source_input_kind": source_input_kind,
        "source_identity": source_identity,
        "title": title,
        "title_source": title_source,
        "slug": slug,
        "capture_date": capture_date,
        "accepted_at": now,
        "canonical_body_sha256": canonical_hash,
        "canonical_body_length": len(canonical_body),
        "anchors": anchors,
        **source_fields,
    }
    (out_dir / "canonical_source.md").write_text(canonical_body + "\n", encoding="utf-8")
    write_json(out_dir / "source_capture_metadata.json", {
        "schema_version": f"compiler_worker_v2_{source_input_kind}_capture_metadata_v1",
        "job_id": job_id,
        "source_id": source_id,
        "source_input_kind": source_input_kind,
        "source_identity": source_identity,
        "capture_date": capture_date,
        "accepted_at": now,
        "canonical_body_sha256": canonical_hash,
        "canonical_body_length": len(canonical_body),
        "title": title,
        "title_source": title_source,
        **capture_metadata,
    })
    write_json(out_dir / "review_packet.json", {
        "schema_version": "compiler_worker_v2_review_packet_v1",
        "job_id": job_id,
        "source_id": source_id,
        "source_input_kind": source_input_kind,
        "source_identity": source_identity,
        "source_review_status": "passed",
        "source_analysis_status": "pending_kp",
        "source_analysis_checker_status": "pending_kp",
        "image_receipt_status": "pending_kp",
        "knowledge_package_status": "pending_kp",
        "coverage_units_status": "pending_kp",
        "canonical_body_sha256": canonical_hash,
        "title": title,
        "title_source": title_source,
        "anchors": anchors,
        **review_fields,
    })
    write_json(out_dir / "input_bundle.json", input_bundle)
    write_json(out_dir / "receipt.json", {
        "schema_version": SCHEMA_VERSION,
        "job_id": job_id,
        "source_id": source_id,
        "source_identity": source_identity,
        "status": "accepted",
        "stage": "input_bundle",
        "checkpoint": "input_bundle",
        "package_dir": out_dir.relative_to(root).as_posix(),
        "blocked_reasons": [],
        "created_at": now,
    })
    bind_package_to_job(root, job_id, source_id, source_input_kind, source_identity, out_dir)
    return out_dir


def command_accept_text(args: argparse.Namespace) -> int:
    root = args.root.resolve()
    source_id = validated_identifier(args.source_id, "source_id")
    source_path = relative_to_root(args.text_file, root, "text input")
    if not source_path.is_file():
        raise CompilerWorkerV2Error("text input file is missing")
    source_bytes = source_path.read_bytes()
    try:
        canonical_body = source_bytes.decode("utf-8-sig").strip()
    except UnicodeDecodeError as exc:
        raise CompilerWorkerV2Error("text input must be UTF-8") from exc
    source_hash = sha256_bytes(source_bytes)
    title = args.title or next(
        (line.strip("# ").strip() for line in canonical_body.splitlines() if line.strip()),
        "User text",
    )
    slug = output_slug(args.slug or slugify(title))
    source_identity = f"user_provided_text:{source_hash}"
    representative_anchors = [
        line.strip() for line in canonical_body.splitlines() if line.strip()
    ][:8]
    existing = existing_input_bundle(root, source_identity)
    if existing:
        print(json.dumps(existing, ensure_ascii=False))
        return 0
    job = ensure_accepted_job(
        root=root,
        job_id=args.job_id,
        source_id=source_id,
        source_input_kind="user_provided_text",
        source_identity=source_identity,
    )
    stage_accepted_input(root, job, source_path, source_bytes)
    out_dir = write_input_bundle(
        root=root,
        source_id=source_id,
        job_id=args.job_id,
        source_input_kind="user_provided_text",
        source_identity=source_identity,
        title=title,
        title_source="user_supplied" if args.title else "source_derived",
        slug=slug,
        canonical_body=canonical_body,
        source_fields={"source_content_sha256": source_hash, "source_retention_mode": "full_body_required", "source_profile": getattr(args, "profile", "article")},
        capture_metadata={
            "source_content_sha256": source_hash,
            "source_retention_mode": "full_body_required",
            "canonical_body_required": True,
            "canonical_body_representative_anchors": representative_anchors,
        },
        review_fields={
            "source_url": "",
            "source_content_sha256": source_hash,
            "source_retention_mode": "full_body_required",
            "canonical_body_required": True,
            "canonical_body_representative_anchors": representative_anchors,
        },
    )
    (out_dir / "source_capture.md").write_bytes(source_bytes)
    print(json.dumps({
        "status": "accepted",
        "job_id": args.job_id,
        "source_identity": source_identity,
        "package_dir": str(out_dir),
    }, ensure_ascii=False))
    return 0


def accepted_body_integrity_issues(
    kp: dict[str, Any], package: Path,
) -> list[tuple[str, str, str]]:
    """Bind a full-body projection to the accepted capture, not an AI summary."""
    bundle_path = package / "input_bundle.json"
    if not bundle_path.is_file():
        return []  # Legacy fixture/revision packages have their own provenance gate.
    try:
        bundle = read_json(bundle_path)
    except (OSError, json.JSONDecodeError):
        return [("input_bundle.json", "accepted input bundle is unreadable", "Restore the accepted input bundle.")]
    if not isinstance(bundle, dict):
        return [("input_bundle.json", "accepted input bundle is not an object", "Restore the accepted input bundle.")]
    source = kp.get("source") if isinstance(kp.get("source"), dict) else {}
    kind = str(bundle.get("source_input_kind") or "")
    if kind not in {"user_provided_text", "url"}:
        return []
    issues: list[tuple[str, str, str]] = []

    def issue(path: str, message: str, fix: str) -> None:
        issues.append((path, message, fix))

    if source.get("source_input_kind") != kind or source.get("source_identity") != bundle.get("source_identity"):
        issue("source.source_identity", "Knowledge Package source differs from the accepted input", "Use the accepted source identity and kind.")
    declared_mode = source.get("retention_mode") or source.get("source_retention_mode")
    if declared_mode != "full_body_required" or (
        source.get("retention_mode") and source.get("source_retention_mode")
        and source["retention_mode"] != source["source_retention_mode"]
    ):
        issue("source.retention_mode", "accepted text/URL source must keep its complete body", "Set full_body_required; preserve the complete accepted body in the source note.")
    if bundle.get("source_retention_mode") not in {None, "full_body_required"}:
        issue("input_bundle.json", "accepted source retention mode was downgraded", "Restore the full-body accepted input bundle.")
    canonical_path = package / "canonical_source.md"
    if not canonical_path.is_file():
        issue("canonical_source.md", "accepted canonical body is missing", "Restore the complete accepted source body.")
        return issues
    canonical = canonical_path.read_text(encoding="utf-8").rstrip("\n")
    if sha256_text(canonical) != bundle.get("canonical_body_sha256"):
        issue("canonical_source.md", "canonical body differs from the accepted input bundle", "Restore the accepted full body; do not replace it with a summary or update its hash.")
    if source.get("canonical_body_sha256") != bundle.get("canonical_body_sha256"):
        issue("source.canonical_body_sha256", "Knowledge Package body hash differs from the accepted input", "Use the accepted full-body hash.")

    capture_path = package / "source_capture.md"
    if not capture_path.is_file():
        issue("source_capture.md", "accepted source capture is missing", "Restore the original accepted capture.")
        return issues
    capture_bytes = capture_path.read_bytes()
    if kind == "user_provided_text":
        if sha256_bytes(capture_bytes) != bundle.get("source_content_sha256"):
            issue("source_capture.md", "captured text bytes differ from the accepted input", "Restore the original accepted text bytes.")
        try:
            expected = capture_bytes.decode("utf-8-sig").strip().replace("\r\n", "\n").replace("\r", "\n").rstrip("\n")
        except UnicodeDecodeError:
            expected = ""
            issue("source_capture.md", "captured text is not UTF-8", "Restore the accepted UTF-8 text.")
        if expected and canonical != expected:
            issue("canonical_source.md", "canonical body omits or rewrites accepted text", "Derive the complete canonical body from source_capture.md; put summaries only in derived fields.")
    else:
        if capture_path.read_text(encoding="utf-8", errors="replace").rstrip("\n") != canonical:
            issue("source_capture.md", "URL article capture differs from the canonical body", "Restore the complete reviewed article capture.")
        metadata_path = package / "source_capture_metadata.json"
        metadata = read_json(metadata_path) if metadata_path.is_file() else {}
        if bundle.get("acquisition_policy") == YOUTUBE_CAPTURE_POLICY:
            policy_fields = ("acquisition_policy", "capture_provider", "submitted_url_attestation", "youtube_video_id", "youtube_caption_language", "youtube_caption_track_kind", "youtube_caption_selection_policy", "youtube_caption_path", "youtube_caption_sha256", "youtube_info_path", "youtube_info_sha256", "yt_dlp_version", "canonical_body_sha256")
            if any(bundle.get(key) != metadata.get(key) for key in policy_fields):
                issue("input_bundle.json", "YouTube caption evidence differs from its accepted metadata", "Restore the matching yt-dlp caption evidence.")
            if bundle.get("capture_provider") != "yt-dlp" or bundle.get("submitted_url_attestation") != YOUTUBE_SUBMITTED_URL_ATTESTATION:
                issue("input_bundle.json", "YouTube capture policy or URL attestation is invalid", "Restore the caption-only capture policy and submitted URL attestation.")
            caption_path, info_path = str(bundle.get("youtube_caption_path") or ""), str(bundle.get("youtube_info_path") or "")
            if caption_path != "source_capture_youtube.vtt" or info_path != "source_capture_youtube.info.json":
                issue("input_bundle.json", "YouTube caption evidence paths are invalid", "Restore the package-local VTT and metadata paths.")
            else:
                vtt_path, raw_info_path = package / caption_path, package / info_path
                if not vtt_path.is_file() or sha256_bytes(vtt_path.read_bytes()) != bundle.get("youtube_caption_sha256"):
                    issue(caption_path, "accepted YouTube caption VTT is missing or changed", "Restore the original yt-dlp VTT capture.")
                if not raw_info_path.is_file() or sha256_bytes(raw_info_path.read_bytes()) != bundle.get("youtube_info_sha256"):
                    issue(info_path, "accepted YouTube metadata is missing or changed", "Restore the original yt-dlp metadata capture.")
                if vtt_path.is_file():
                    try:
                        recovered = vtt_to_canonical_body(vtt_path.read_bytes(), normalized_url=str(bundle.get("normalized_url") or ""), video_id=str(bundle.get("youtube_video_id") or ""), language=str(bundle.get("youtube_caption_language") or ""), track_kind=str(bundle.get("youtube_caption_track_kind") or ""))
                    except ValueError:
                        issue(caption_path, "accepted YouTube caption VTT cannot be replayed", "Restore the readable full VTT caption capture.")
                    else:
                        if recovered != canonical:
                            issue("canonical_source.md", "YouTube transcript differs from the accepted VTT capture", "Restore the complete timestamped transcript generated from the VTT capture.")
        elif bundle.get("acquisition_policy") == DIRECT_HTML_ONLY_CAPTURE_POLICY:
            policy_fields = ("acquisition_policy", "capture_provider", "provider_attempts", "raw_html_capture_path", "raw_html_sha256", "canonical_body_sha256")
            if any(bundle.get(key) != metadata.get(key) for key in policy_fields):
                issue("input_bundle.json", "URL direct-only acquisition evidence differs from its accepted metadata", "Restore the matching direct-only HTML evidence.")
            if (bundle.get("capture_provider") != "compiler-worker-v2-direct" or bundle.get("provider_attempts") != []
                    or bundle.get("raw_html_capture_path") != "source_capture.html"
                    or not re.fullmatch(r"[0-9a-f]{64}", str(bundle.get("raw_html_sha256") or ""))
                    or bundle.get("api_capture_path")):
                issue("input_bundle.json", "direct-only capture evidence is invalid", "Restore the direct-only HTML capture without API attempt records.")
        elif bundle.get("acquisition_policy") or metadata.get("acquisition_policy") or bundle.get("api_capture_path"):
            policy_fields = ("acquisition_policy", "capture_provider", "provider_attempts", "api_capture_path", "api_capture_sha256", "raw_html_capture_path", "raw_html_sha256", "canonical_body_sha256")
            if (bundle.get("acquisition_policy") != "api_first_html_fallback_v1"
                    or any(bundle.get(key) != metadata.get(key) for key in policy_fields)):
                issue("input_bundle.json", "URL acquisition evidence differs from its accepted metadata", "Restore the matching accepted API-first evidence.")
            attempts = bundle.get("provider_attempts")
            provider = bundle.get("capture_provider")
            expected = {"firecrawl": ["firecrawl"], "tavily": ["firecrawl", "tavily"], "compiler-worker-v2-direct": ["firecrawl", "tavily"]}.get(provider)
            valid = isinstance(attempts, list) and all(isinstance(item, dict) for item in attempts)
            if valid:
                valid = expected is not None and [item.get("provider") for item in attempts] == expected
            if valid:
                valid = all(item.get("status") in {"failed", "unavailable"} for item in (attempts if provider == "compiler-worker-v2-direct" else attempts[:-1]))
                if provider != "compiler-worker-v2-direct":
                    valid = valid and attempts[-1].get("status") == "captured"
            if not valid:
                issue("input_bundle.json", "URL acquisition did not follow API-first order", "Restore the recorded API attempts; HTML requires both providers exhausted.")
            if provider in {"firecrawl", "tavily"}:
                if bundle.get("api_capture_path") != "source_capture_api.json" or not re.fullmatch(r"[0-9a-f]{64}", str(bundle.get("api_capture_sha256") or "")) or bundle.get("raw_html_capture_path"):
                    issue("input_bundle.json", "API capture requires its immutable raw response", "Restore the full API response path and hash.")
            elif provider == "compiler-worker-v2-direct":
                if bundle.get("raw_html_capture_path") != "source_capture.html" or not re.fullmatch(r"[0-9a-f]{64}", str(bundle.get("raw_html_sha256") or "")) or bundle.get("api_capture_path"):
                    issue("input_bundle.json", "direct fallback requires its immutable HTML snapshot", "Restore the fallback HTML path and hash.")
        api_capture = str(bundle.get("api_capture_path") or "")
        if api_capture:
            if api_capture != "source_capture_api.json":
                issue("input_bundle.json", "URL API snapshot path is invalid", "Restore the accepted package-local API response.")
                return issues
            raw_path = package / api_capture
            if not raw_path.is_file() or sha256_bytes(raw_path.read_bytes()) != bundle.get("api_capture_sha256"):
                issue(api_capture, "original URL API response is missing or changed", "Restore the accepted API response.")
            else:
                try:
                    recovered = parse_api_capture(bundle["capture_provider"], read_json(raw_path), bundle["normalized_url"])
                    if recovered.markdown.replace("\r\n", "\n").replace("\r", "\n").strip() != canonical:
                        issue("canonical_source.md", "URL body differs from the accepted API response", "Restore the complete provider body; do not summarize it.")
                except Exception:
                    issue(api_capture, "accepted URL API response cannot be replayed", "Restore the accepted provider response and identity.")
        raw_capture = str(bundle.get("raw_html_capture_path") or "")
        if raw_capture:
            if raw_capture != "source_capture.html":
                issue("input_bundle.json", "URL HTML snapshot path is invalid", "Use the accepted package-local HTML snapshot.")
                return issues
            raw_path = package / raw_capture
            if not raw_path.is_file() or sha256_bytes(raw_path.read_bytes()) != bundle.get("raw_html_sha256"):
                issue(raw_capture, "original URL HTML snapshot is missing or changed", "Restore the accepted HTML snapshot before continuing.")
            else:
                try:
                    recovered = extract_article_text(raw_path.read_bytes().decode("utf-8", errors="replace"), str(bundle.get("title") or ""))
                except CompilerWorkerV2Error:
                    issue(raw_capture, "accepted URL HTML no longer yields a readable article", "Restore the accepted article snapshot and review its completeness.")
                else:
                    if recovered != canonical:
                        issue("canonical_source.md", "URL body differs from the accepted HTML extraction", "Restore the full article extracted from the accepted HTML snapshot.")
    return issues


def command_accept_image(args: argparse.Namespace) -> int:
    root = args.root.resolve()
    source_id = validated_identifier(args.source_id, "source_id")
    image_path = relative_to_root(args.image_file, root, "image input")
    analysis_path = (
        relative_to_root(args.image_analysis_file, root, "image analysis")
        if args.image_analysis_file
        else None
    )
    if not image_path.is_file() or (analysis_path is not None and not analysis_path.is_file()):
        raise CompilerWorkerV2Error("image input or image analysis file is missing")
    image_bytes = image_path.read_bytes()
    source_hash = sha256_bytes(image_bytes)
    try:
        inspection = inspect_image(image_path)
    except Exception as exc:
        raise CompilerWorkerV2Error(f"image readback failed: {exc}") from exc
    source_identity = f"user_provided_image:{source_hash}"
    existing = existing_input_bundle(root, source_identity)
    if existing:
        print(json.dumps(existing, ensure_ascii=False))
        return 0
    job = ensure_accepted_job(
        root=root,
        job_id=args.job_id,
        source_id=source_id,
        source_input_kind="user_provided_image",
        source_identity=source_identity,
    )
    stage_accepted_input(root, job, image_path, image_bytes)
    slug = output_slug(args.slug or slugify(source_id))
    out_dir = package_dir(root, source_id, slug)
    if analysis_path is None:
        extension = inspection.extension
        original_name = f"{source_id}_source_original{extension}"
        asset_dir = out_dir / "assets"
        asset_dir.mkdir(parents=True, exist_ok=True)
        (asset_dir / original_name).write_bytes(image_bytes)
        (out_dir / f"source_capture{extension}").write_bytes(image_bytes)
        write_json(out_dir / "input_bundle.json", {
            "schema_version": SCHEMA_VERSION,
            "stage": "source_captured",
            "status": "awaiting_image_analysis",
            "requested_title": args.title,
            "job_id": args.job_id,
            "source_id": source_id,
            "source_input_kind": "user_provided_image",
            "source_identity": source_identity,
            "source_image_sha256": source_hash,
            "source_image_mime": inspection.mime_type,
            "source_image_dimensions": {"width": inspection.width, "height": inspection.height},
            "original_image_path": f"assets/{original_name}",
        })
        bind_package_to_job(
            root,
            args.job_id,
            source_id,
            "user_provided_image",
            source_identity,
            out_dir,
        )
        print(json.dumps({
            "status": "awaiting_image_analysis",
            "job_id": args.job_id,
            "source_identity": source_identity,
            "package_dir": str(out_dir),
            "write_performed": True,
        }, ensure_ascii=False))
        return 0
    analysis = read_json(analysis_path)
    analysis_issues = validate_visual_analysis(
        analysis,
        expected_image_sha256=inspection.sha256,
        expected_width=inspection.width,
        expected_height=inspection.height,
    )
    if analysis_issues:
        raise CompilerWorkerV2Error(
            "image analysis contract failed: "
            + "; ".join(str(issue.get("code") or "invalid") for issue in analysis_issues)
        )
    canonical_lines = [
        str(analysis.get("title") or args.title or "User image"),
        str(analysis.get("summary") or ""),
        *[
            str(item.get("text") or "")
            for item in analysis.get("transcription", [])
            if isinstance(item, dict)
        ],
        *[
            str(item.get("claim") or "")
            for item in analysis.get("source_claims", [])
            if isinstance(item, dict)
        ],
    ]
    canonical_body = "\n".join(line.strip() for line in canonical_lines if line.strip())
    title = args.title or str(analysis.get("title") or "User image")
    slug = output_slug(args.slug or slugify(source_id))
    mime = inspection.mime_type
    extension = inspection.extension
    out_dir = write_input_bundle(
        root=root,
        source_id=source_id,
        job_id=args.job_id,
        source_input_kind="user_provided_image",
        source_identity=source_identity,
        title=title,
        title_source="user_supplied" if args.title else "source_derived",
        slug=slug,
        canonical_body=canonical_body,
        source_fields={
            "source_profile": getattr(args, "profile", "article"),
            "source_image_sha256": source_hash,
            "source_image_mime": mime,
            "source_image_dimensions": {"width": inspection.width, "height": inspection.height},
            "source_retention_mode": "original_image_required",
        },
        capture_metadata={
            "source_image_sha256": source_hash,
            "source_image_mime": mime,
            "source_image_dimensions": {"width": inspection.width, "height": inspection.height},
            "source_retention_mode": "original_image_required",
        },
        review_fields={
            "source_url": "",
            "source_image_sha256": source_hash,
            "source_retention_mode": "original_image_required",
            "image_analysis_status": "complete",
            "image_analysis_checker_status": "passed",
        },
    )
    asset_dir = out_dir / "assets"
    asset_dir.mkdir(parents=True, exist_ok=True)
    original_name = f"{source_id}_source_original{extension}"
    (asset_dir / original_name).write_bytes(image_bytes)
    (out_dir / f"source_capture{extension}").write_bytes(image_bytes)
    analysis["source_image_sha256"] = source_hash
    analysis["image_dimensions"] = {"width": inspection.width, "height": inspection.height}
    write_json(out_dir / "image_analysis.json", analysis)
    packet = read_json(out_dir / "review_packet.json")
    packet["original_image_path"] = f"assets/{original_name}"
    packet["image_analysis_sha256"] = canonical_analysis_sha256(analysis)
    write_json(out_dir / "review_packet.json", packet)
    bundle = read_json(out_dir / "input_bundle.json")
    bundle["original_image_path"] = f"assets/{original_name}"
    bundle["image_analysis_sha256"] = canonical_analysis_sha256(analysis)
    write_json(out_dir / "input_bundle.json", bundle)
    print(json.dumps({
        "status": "accepted",
        "job_id": args.job_id,
        "source_identity": source_identity,
        "package_dir": str(out_dir),
    }, ensure_ascii=False))
    return 0


def command_accept_url(args: argparse.Namespace) -> int:
    root = args.root.resolve()
    capture_mode = str(getattr(args, "capture_mode", URL_CAPTURE_API_FIRST) or URL_CAPTURE_API_FIRST)
    if capture_mode not in {URL_CAPTURE_API_FIRST, URL_CAPTURE_DIRECT_HTML_ONLY}:
        raise CompilerWorkerV2Error("URL capture mode is invalid")
    source_id = validated_identifier(args.source_id, "source_id")
    normalized_url = normalize_url(args.url)
    case_evidence_file = (
        relative_to_root(args.case_evidence_file, root, "case evidence manifest")
        if getattr(args, "case_evidence_file", None)
        else None
    )
    if case_evidence_file:
        try:
            validate_case_evidence_manifest(root, case_evidence_file, primary_url=normalized_url)
        except CaseEvidenceError as exc:
            raise CompilerWorkerV2Error(f"case evidence manifest invalid: {exc}") from exc
    source_url_hash = sha256_text(normalized_url)
    source_identity = f"url:{source_url_hash}"
    existing = existing_input_bundle(root, source_identity)
    if existing:
        print(json.dumps(existing, ensure_ascii=False))
        return 0
    now = datetime.now(timezone.utc).isoformat()
    capture_date = date.today().isoformat()
    raw_html_bytes = None
    raw_api_bytes = None
    raw_youtube_vtt = None
    raw_youtube_info = None
    youtube_capture = getattr(args, "youtube_capture", None)
    if is_youtube_url(normalized_url):
        if youtube_capture is None:
            try:
                youtube_capture = capture_youtube_transcript(normalized_url, temporary_root=root / DEFAULT_PACKAGE_ROOT / "_youtube_caption_tmp", timeout_seconds=args.timeout_seconds)
            except ValueError as exc:
                raise CompilerWorkerV2Error(str(exc)) from exc
        if not isinstance(youtube_capture, YoutubeTranscriptCapture):
            raise CompilerWorkerV2Error("YouTube caption capture result is invalid")
        if youtube_capture.normalized_url != normalized_url:
            raise CompilerWorkerV2Error("YouTube caption capture URL differs from the submitted URL")
        raw_youtube_vtt, raw_youtube_info = youtube_capture.raw_vtt, youtube_capture.info_json
        canonical_body, extracted_title = youtube_capture.canonical_body, youtube_capture.title
        status, content_type, final_url = 200, "text/vtt", normalized_url
        capture_proof = {"capture_provider": "yt-dlp", "acquisition_policy": YOUTUBE_CAPTURE_POLICY,
            "submitted_url_attestation": YOUTUBE_SUBMITTED_URL_ATTESTATION, "youtube_video_id": youtube_capture.video_id,
            "youtube_caption_language": youtube_capture.selected_language, "youtube_caption_track_kind": youtube_capture.selected_track_kind,
            "youtube_caption_selection_policy": "declared_source_language_manual_before_automatic_v2", "youtube_caption_path": "source_capture_youtube.vtt",
            "youtube_caption_sha256": sha256_bytes(raw_youtube_vtt), "youtube_info_path": "source_capture_youtube.info.json",
            "youtube_info_sha256": sha256_bytes(raw_youtube_info), "yt_dlp_version": youtube_capture.yt_dlp_version,
            "youtube_channel": youtube_capture.channel, "youtube_upload_date": youtube_capture.upload_date}
    else:
        acquisition: dict[str, Any] = {}
        if capture_mode == URL_CAPTURE_DIRECT_HTML_ONLY:
            request = urllib.request.Request(normalized_url, headers={"User-Agent": "Agent-Handoff-Kit-Compiler-Worker-v2/0.1"})
            with urllib.request.urlopen(request, timeout=args.timeout_seconds) as response:
                raw_html_bytes, status, final_url, content_type = response.read(), getattr(response, "status", 200), normalize_url(response.geturl()), response.headers.get("content-type", "")
            raw_html = raw_html_bytes.decode("utf-8", errors="replace")
            extracted_title = extract_title(raw_html)
            canonical_body = extract_article_text(raw_html, args.title or extracted_title)
            validate_url_capture(status=status, content_type=content_type, raw_html=raw_html, canonical_body=canonical_body, extracted_title=extracted_title)
            capture_proof = {
                "capture_provider": "compiler-worker-v2-direct",
                "raw_html_capture_path": "source_capture.html",
                "raw_html_sha256": sha256_bytes(raw_html_bytes),
                "provider_attempts": [],
                "acquisition_policy": DIRECT_HTML_ONLY_CAPTURE_POLICY,
                "api_elapsed_seconds": None,
            }
        else:
            acquisition = capture_url_api_first(normalized_url, timeout_seconds=args.timeout_seconds)
            if acquisition["status"] == "captured":
                raw_api_bytes = (json.dumps(acquisition["raw_response"], ensure_ascii=False, indent=2) + "\n").encode("utf-8")
                canonical_body, extracted_title, status, content_type = acquisition["markdown"], acquisition["title"], 200, "text/markdown"
                final_url = normalize_url(acquisition["response_url"])
                capture_proof = {"capture_provider": acquisition["provider"], "api_capture_path": "source_capture_api.json", "api_capture_sha256": sha256_bytes(raw_api_bytes)}
            elif acquisition["status"] == "fallback_required":
                attempted = [item.get("provider") for item in acquisition["attempts"]]
                if attempted != ["firecrawl", "tavily"] or any(item.get("status") == "captured" for item in acquisition["attempts"]):
                    raise CompilerWorkerV2Error("direct fallback requires recorded API exhaustion")
                request = urllib.request.Request(normalized_url, headers={"User-Agent": "Agent-Handoff-Kit-Compiler-Worker-v2/0.1"})
                with urllib.request.urlopen(request, timeout=args.timeout_seconds) as response:
                    raw_html_bytes, status, final_url, content_type = response.read(), getattr(response, "status", 200), normalize_url(response.geturl()), response.headers.get("content-type", "")
                raw_html = raw_html_bytes.decode("utf-8", errors="replace")
                extracted_title = extract_title(raw_html)
                canonical_body = extract_article_text(raw_html, args.title or extracted_title)
                validate_url_capture(status=status, content_type=content_type, raw_html=raw_html, canonical_body=canonical_body, extracted_title=extracted_title)
                capture_proof = {"capture_provider": "compiler-worker-v2-direct", "raw_html_capture_path": "source_capture.html", "raw_html_sha256": sha256_bytes(raw_html_bytes)}
            else:
                raise CompilerWorkerV2Error("unknown URL acquisition result; direct fallback is not authorized")
            capture_proof.update(provider_attempts=acquisition["attempts"], acquisition_policy="api_first_html_fallback_v1", api_elapsed_seconds=acquisition.get("elapsed_seconds"))
    ensure_accepted_job(root=root, job_id=args.job_id, source_id=source_id, source_input_kind="url", source_identity=source_identity, source_url=normalized_url)
    capture_job = read_json(job_file(root, args.job_id))
    capture_job["last_url_acquisition"] = {"observed_at": iso_now(), "status": "captured", "capture_provider": capture_proof["capture_provider"], "acquisition_policy": capture_proof["acquisition_policy"]}
    write_v2_job(root, capture_job)
    title = args.title or extracted_title
    title_source = "user_supplied" if args.title else "source_declared"
    if not args.title and not is_youtube_url(normalized_url) and acquisition.get("title_origin") == "url_fallback":
        title_source = "source_derived"
    slug = output_slug(args.slug or slugify(title))
    anchors = [line for line in canonical_body.splitlines() if line.strip()][:8]

    out_dir = package_dir(root, source_id, slug)
    case_evidence_binding: dict[str, Any] = {"asset_mode": ASSET_MODE_FORMAL}
    if case_evidence_file:
        try:
            case_evidence_binding = stage_case_evidence_manifest(
                root,
                case_evidence_file,
                out_dir,
                primary_url=normalized_url,
            )
        except CaseEvidenceError as exc:
            raise CompilerWorkerV2Error(f"case evidence staging failed: {exc}") from exc
    input_bundle = {
        "schema_version": SCHEMA_VERSION,
        "stage": "input_bundle",
        "status": "accepted",
        "job_id": args.job_id,
        "source_id": source_id,
        "source_input_kind": "url",
        "source_url": normalized_url,
        "normalized_url": normalized_url,
        "final_url": final_url,
        "source_url_hash": source_url_hash,
        "source_identity": source_identity,
        "title": title,
        "title_source": title_source,
        "slug": slug,
        "capture_date": capture_date,
        "accepted_at": now,
        "http_status": status,
        "content_type": content_type,
        **capture_proof,
        "execution_policy_version": EXECUTION_CONTRACT_VERSION,
        "source_profile": getattr(args, "profile", "article"),
        "source_retention_mode": "full_body_required",
        "canonical_body_sha256": sha256_text(canonical_body),
        "canonical_body_length": len(canonical_body),
        "anchors": anchors,
        **case_evidence_binding,
    }
    write_json(out_dir / "input_bundle.json", input_bundle)
    if raw_html_bytes is not None:
        (out_dir / "source_capture.html").write_bytes(raw_html_bytes)
    if raw_api_bytes is not None:
        (out_dir / "source_capture_api.json").write_bytes(raw_api_bytes)
    if raw_youtube_vtt is not None:
        (out_dir / "source_capture_youtube.vtt").write_bytes(raw_youtube_vtt)
    if raw_youtube_info is not None:
        (out_dir / "source_capture_youtube.info.json").write_bytes(raw_youtube_info)
    (out_dir / "canonical_source.md").write_text(canonical_body + "\n", encoding="utf-8")
    (out_dir / "source_capture.md").write_text(canonical_body + "\n", encoding="utf-8")
    capture_metadata = {
        "schema_version": "compiler_worker_v2_url_capture_metadata_v1",
        "job_id": args.job_id,
        "source_id": source_id,
        "source_input_kind": "url",
        "url": normalized_url,
        "normalized_url": normalized_url,
        "final_url": final_url,
        "source_url_hash": source_url_hash,
        "source_identity": source_identity,
        "source_url_binding_status": "verified",
        **capture_proof,
        "http_status": status,
        "content_type": content_type,
        "capture_date": capture_date,
        "accepted_at": now,
        "source_retention_mode": "full_body_required",
        "canonical_body_required": True,
        "canonical_body_representative_anchors": anchors,
        "canonical_body_sha256": sha256_text(canonical_body),
        "canonical_body_length": len(canonical_body),
        "title": title,
        "title_source": title_source,
        **case_evidence_binding,
    }
    write_json(out_dir / "source_capture_metadata.json", capture_metadata)
    review_packet = {
        "schema_version": "compiler_worker_v2_review_packet_v1",
        "job_id": args.job_id,
        "source_id": source_id,
        "source_input_kind": "url",
        "source_url": normalized_url,
        "normalized_url": normalized_url,
        "source_url_hash": source_url_hash,
        "source_identity": source_identity,
        "source_url_binding_status": "verified",
        "source_review_status": "passed",
        "source_access_status": "public_ok",
        "source_analysis_status": "pending_kp",
        "source_analysis_checker_status": "pending_kp",
        "image_receipt_status": "pending_kp",
        "knowledge_package_status": "pending_kp",
        "coverage_units_status": "pending_kp",
        "source_retention_mode": "full_body_required",
        "canonical_body_required": True,
        "canonical_body_representative_anchors": anchors,
        "canonical_body_sha256": sha256_text(canonical_body),
        "title": title,
        "title_source": title_source,
        "anchors": anchors,
        **case_evidence_binding,
    }
    write_json(out_dir / "review_packet.json", review_packet)
    receipt = {
        "schema_version": SCHEMA_VERSION,
        "job_id": args.job_id,
        "source_id": source_id,
        "source_identity": source_identity,
        "status": "accepted",
        "stage": "input_bundle",
        "checkpoint": "input_bundle",
        "package_dir": out_dir.relative_to(root).as_posix(),
        "blocked_reasons": [],
        "created_at": now,
    }
    write_json(out_dir / "receipt.json", receipt)
    bind_package_to_job(root, args.job_id, source_id, "url", source_identity, out_dir)
    accepted_job = read_json(job_file(root, args.job_id))
    accepted_job["asset_mode"] = case_evidence_binding["asset_mode"]
    if case_evidence_binding["asset_mode"] == ASSET_MODE_CASE:
        accepted_job["case_evidence_manifest_path"] = (out_dir / case_evidence_binding["case_evidence_manifest_path"]).relative_to(root).as_posix()
    write_v2_job(root, accepted_job)
    print(json.dumps({"status": "accepted", "job_id": args.job_id, "package_dir": str(out_dir)}, ensure_ascii=False))
    return 0


def validate_case_evidence_kp(kp: dict[str, Any], package: Path) -> list[str]:
    """Validate the evidence branch without weakening the formal image branch."""
    errors: list[str] = []
    source = kp.get("source") if isinstance(kp.get("source"), dict) else {}
    if source.get("source_input_kind") != "url":
        errors.append("case_evidence is only supported for URL sources")
    if kp.get("infographics"):
        errors.append("case_evidence must not carry infographic pages")
    if (kp.get("projection") or {}).get("asset_mode") != ASSET_MODE_CASE:
        errors.append("case_evidence projection asset mode mismatch")
    evidence = kp.get("case_evidence")
    if not isinstance(evidence, dict):
        return [*errors, "case_evidence record is missing"]
    for key in ("manifest_path", "manifest_sha256", "creator", "screenshots", "technical_references", "reproduction", "method_card"):
        if key not in evidence:
            errors.append(f"case_evidence missing {key}")
    method_card = evidence.get("method_card") if isinstance(evidence.get("method_card"), dict) else {}
    for key in ("title", "purpose", "observed_design", "reproduction_method", "boundary"):
        if not str(method_card.get(key) or "").strip():
            errors.append(f"case_evidence.method_card missing {key}")
    if evidence.get("manifest_path") != source.get("case_evidence_manifest_path"):
        errors.append("case_evidence manifest path differs from accepted source")
    if evidence.get("manifest_sha256") != source.get("case_evidence_manifest_sha256"):
        errors.append("case_evidence manifest hash differs from accepted source")
    selection_contract: dict[str, Any] | None = None
    revision = kp.get("revision") if isinstance(kp.get("revision"), dict) else {}
    if revision.get("kind") == "case_revalidation":
        raw_contract = revision.get("selection_contract")
        try:
            selection_contract = validate_selection_contract(raw_contract)
        except CaseEvidenceError as exc:
            return [*errors, f"case revalidation selection contract invalid: {exc}"]
        if revision.get("selection_contract_sha256") != canonical_payload_sha256(selection_contract):
            errors.append("case revalidation selection contract hash differs from the revision")
        for field in ("revision_id", "original_completion_path", "original_completion_receipt_sha256", "revision_plan_sha256"):
            if not str(revision.get(field) or "").strip():
                errors.append(f"case revalidation revision missing {field}")
    try:
        manifest = read_case_evidence_manifest(package, source, selection_contract=selection_contract)
    except CaseEvidenceError as exc:
        return [*errors, f"case evidence manifest invalid: {exc}"]
    for key in ("creator", "screenshots", "technical_references", "reproduction"):
        if evidence.get(key) != manifest.get(key):
            errors.append(f"case_evidence {key} differs from the immutable manifest")
    return errors


def validate_kp(kp: dict[str, Any], package: Path) -> list[str]:
    errors: list[str] = []
    image_anchor_ids: set[str] = set()
    image_anchor_ids: set[str] = set()
    if kp.get("schema_version") != KP_SCHEMA_VERSION:
        errors.append("invalid KP schema_version")
    source = kp.get("source") or {}
    asset_mode = source.get("asset_mode", ASSET_MODE_FORMAL)
    if asset_mode not in {ASSET_MODE_FORMAL, ASSET_MODE_CASE}:
        errors.append("unsupported source.asset_mode")
    if not (package / "completion_receipt.json").is_file():
        try:
            from tools.ingest_plan_contract import content_errors
        except ModuleNotFoundError:
            from ingest_plan_contract import content_errors
        errors.extend(content_errors(kp, package))
    for field in ("source_id", "source_input_kind", "source_identity", "title", "title_source", "canonical_body_sha256"):
        if not source.get(field):
            errors.append(f"missing source.{field}")
    if source.get("source_input_kind") not in SUPPORTED_INPUT_KINDS:
        errors.append("unsupported source.source_input_kind")
    if source.get("title_source") not in {
        "source_declared",
        "source_derived",
        "user_supplied",
        "ai_generated_from_source",
        "ai_proposed",
    }:
        errors.append("unsupported source.title_source")
    canonical_source = package / "canonical_source.md"
    canonical_body = ""
    if not canonical_source.exists():
        errors.append("missing canonical_source.md")
    else:
        canonical_body = canonical_source.read_text(encoding="utf-8").rstrip("\n")
        actual = sha256_text(canonical_body)
        if actual != source.get("canonical_body_sha256"):
            errors.append("canonical body hash mismatch")
    errors.extend(message for _, message, _ in accepted_body_integrity_issues(kp, package))
    source_input_kind = source.get("source_input_kind")
    if source_input_kind == "url":
        for field in ("normalized_url", "source_url_hash"):
            if not source.get(field):
                errors.append(f"missing source.{field}")
        normalized_url = source.get("normalized_url", "")
        if normalized_url and source.get("source_url_hash") != sha256_text(normalized_url):
            errors.append("normalized URL hash mismatch")
        if source.get("source_identity") != f"url:{source.get('source_url_hash')}":
            errors.append("source identity mismatch")
    elif source_input_kind == "user_provided_text":
        if source.get("readability_status") != "user_text_complete":
            errors.append("user text source must be formally reviewed")
        content_hash = str(source.get("source_content_sha256") or "")
        capture = package / "source_capture.md"
        if not re.fullmatch(r"[0-9a-f]{64}", content_hash):
            errors.append("invalid source.source_content_sha256")
        elif not capture.exists() or sha256_bytes(capture.read_bytes()) != content_hash:
            errors.append("user text source capture hash mismatch")
        if source.get("source_identity") != f"user_provided_text:{content_hash}":
            errors.append("source identity mismatch")
        origin_hint = extract_user_text_origin_hint(canonical_body)
        errors.extend(
            validate_user_text_citation_evidence(
                source,
                required=bool(origin_hint),
            )
        )
        if origin_hint or isinstance(source.get("citation_evidence"), dict):
            errors.extend(validate_user_text_external_origin(source))
        if origin_hint:
            errors.extend(validate_user_text_origin_hint(source, origin_hint))
    elif source_input_kind == "user_provided_image":
        if source.get("readability_status") != "user_image_complete":
            errors.append("user image source must be formally reviewed")
        image_hash = str(source.get("source_image_sha256") or "")
        metadata_path = package / "source_capture_metadata.json"
        metadata = read_json(metadata_path) if metadata_path.exists() else {}
        original_path = str(
            metadata.get("original_image_path")
            or (read_json(package / "review_packet.json").get("original_image_path") if (package / "review_packet.json").exists() else "")
        )
        if not re.fullmatch(r"[0-9a-f]{64}", image_hash):
            errors.append("invalid source.source_image_sha256")
        if source.get("source_identity") != f"user_provided_image:{image_hash}":
            errors.append("source identity mismatch")
        if not original_path:
            errors.append("missing original image path")
        else:
            original = package_relative_path(package, original_path, "original image")
            if not original.exists() or sha256_bytes(original.read_bytes()) != image_hash:
                errors.append("user image source capture hash mismatch")
        analysis_path = package / "image_analysis.json"
        if not analysis_path.exists():
            errors.append("missing image_analysis.json")
        else:
            analysis = read_json(analysis_path)
            if analysis.get("analysis_status") != "complete" or analysis.get("checker_status") != "passed":
                errors.append("image analysis checker is not passed")
            image_anchor_ids = {
                str(anchor.get("id") or "").strip()
                for anchor in analysis.get("anchors") or []
                if isinstance(anchor, dict) and str(anchor.get("id") or "").startswith("IMG-")
            }
    if canonical_body:
        errors.extend(validate_coverage_units(kp, canonical_body))
    if not kp.get("knowledge"):
        errors.append("missing knowledge items")
    elif source_input_kind == "user_provided_image":
        for index, item in enumerate(kp.get("knowledge") or [], start=1):
            if not isinstance(item, dict):
                continue
            evidence = str(item.get("evidence_anchor") or "")
            if not any(anchor_id in evidence for anchor_id in image_anchor_ids):
                item_id = str(item.get("kp_id") or index)
                errors.append(f"image knowledge item {item_id} missing visual IMG-* evidence anchor")
    if not kp.get("projection"):
        errors.append("missing projection plan")
    if asset_mode == ASSET_MODE_CASE:
        errors.extend(validate_case_evidence_kp(kp, package))
        return errors
    infographics = kp.get("infographics") or []
    if not infographics:
        errors.append("missing infographic plan")
    coverage_ids = {
        str(item.get("id") or "")
        for item in kp.get("coverage_units") or []
        if isinstance(item, dict)
    }
    analysis = kp.get("analysis") if isinstance(kp.get("analysis"), dict) else {}
    coverage_ids.update(
        str(item.get("id") or "")
        for item in analysis.get("source_claims") or []
        if isinstance(item, dict)
    )
    current_contract_pages = [
        infographic
        for infographic in infographics
        if isinstance(infographic, dict)
        and infographic_uses_current_prompt_contract(infographic, package)
    ]
    completed_package = (package / "completion_receipt.json").is_file()
    if not completed_package:
        for page_index, infographic in enumerate(infographics, start=1):
            if (
                isinstance(infographic, dict)
                and infographic.get("status") == "receipt_verified"
                and not infographic_uses_current_prompt_contract(infographic, package)
                and not legacy_page_has_current_core_confirmation(
                    kp, infographic, package
                )
            ):
                errors.append(
                    f"infographic {page_index} does not use the current prompt contract or a current core review confirmation"
                )
    if current_contract_pages:
        errors.extend(validate_primary_work_metadata(source, canonical_body))
        errors.extend(validate_coverage_audit(kp))
        errors.extend(
            validate_content_map(
                kp.get("coverage_units") or [],
                infographics,
                canonical_body,
            )
        )
        errors.extend(validate_multi_page_orientation(infographics))
        for infographic in infographics:
            if isinstance(infographic, dict):
                errors.extend(
                    validate_reader_metadata(source, infographic, canonical_body)
                )
        revision = kp.get("revision") if isinstance(kp.get("revision"), dict) else {}
        slug = str(revision.get("asset_slug") or "").strip()
        if not slug:
            slug = package.name.split("_", 1)[1] if "_" in package.name else ""
        for page_index, infographic in enumerate(infographics, start=1):
            if not completed_package:
                errors.extend(validate_compiled_infographic_page(kp, package, infographic))
            expected_name = canonical_infographic_asset_filename(
                str(source.get("source_id") or ""),
                slug,
                page_index,
                len(infographics),
            )
            if Path(str(infographic.get("asset_path") or "")).name != expected_name:
                errors.append(
                    f"infographic {page_index} asset path does not use canonical source filename"
                )
    for index, infographic in enumerate(infographics, start=1):
        if infographic.get("status") != "receipt_verified":
            errors.append(f"infographic {index} lacks verified gpt-image-2 receipt")
            continue
        infographic_coverage = set(string_list(infographic.get("coverage_ids")))
        if not infographic_coverage:
            errors.append(f"infographic {index} missing coverage_ids")
        elif infographic_coverage - coverage_ids:
            errors.append(f"infographic {index} references unknown coverage_ids")
        if not string_list(infographic.get("visible_text")):
            errors.append(f"infographic {index} missing visible_text")
        primary_work_title = str(source.get("primary_work_title") or "").strip()
        if primary_work_title and infographic.get("primary_work_title") != primary_work_title:
            errors.append(f"infographic {index} primary_work_title does not match source")
        if infographic.get("title_source") in AUTHORITATIVE_SOURCE_TITLE_SOURCES:
            if source.get("title_source") != infographic.get("title_source"):
                errors.append(f"infographic {index} cannot promote a non-authoritative title")
            elif infographic.get("title") != reader_infographic_title(source.get("title")):
                errors.append(f"infographic {index} authoritative title does not match source")
        if primary_work_title and infographic.get("title_source") != "primary_work":
            errors.append(f"infographic {index} primary work title source is not declared")
        errors.extend(
            validate_infographic_receipt(
                infographic,
                index,
                package,
                primary_work_title=primary_work_title or None,
                source=source,
            )
        )
    errors.extend(validate_image_attempt_ledger(kp, package))
    return errors


def validate_user_text_external_origin(source: dict[str, Any]) -> list[str]:
    """Prevent confusing the user who pasted text with the cited source."""

    errors: list[str] = []
    author = str(source.get("author") or "").strip()
    source_label = str(source.get("source_label") or "").strip()
    content_owner = str(source.get("content_owner") or "").strip()
    title = str(source.get("title") or "").strip()
    if "使用者" in author or author.startswith(("使用者", "用戶")):
        errors.append("user text external source author must name the original author, not the submitting user")
    if source_label.startswith(("使用者", "使用者", "用戶")) or "使用者 評註" in source_label:
        errors.append("user text external source_label must name the original publication/source, not the submitting user")
    if "使用者" in content_owner or content_owner.startswith(("使用者", "用戶")):
        errors.append("user text content owner must derive from source material, not the submitting user")
    if re.search(r"^[^｜|]{2,80}[｜|]\s*\S", title):
        errors.append("user text external source title must be the work title only, not author-title combined")
    return errors


def validate_user_text_citation_evidence(
    source: dict[str, Any],
    *,
    required: bool = False,
) -> list[str]:
    """Keep a cited work URL separate from the identity of pasted text."""

    errors: list[str] = []
    if str(source.get("source_url") or "").strip():
        errors.append("user text source_url is reserved for URL ingestion")

    if str(source.get("original_source_url") or "").strip() or str(
        source.get("original_source_url_status") or ""
    ).strip():
        errors.append("user text citation evidence must be nested under source.citation_evidence")

    citation = source.get("citation_evidence")
    citation = citation if isinstance(citation, dict) else {}
    evidence_declared = bool(citation)
    if not (required or evidence_declared):
        return errors

    for field in ("title", "authors", "publication"):
        if not str(citation.get(field) or "").strip():
            errors.append(f"user text citation requires source.citation_evidence.{field}")

    original_url = str(citation.get("original_source_url") or "").strip()
    url_status = str(citation.get("original_source_url_status") or "").strip()
    if not original_url:
        errors.append("user text citation requires source.citation_evidence.original_source_url")
    else:
        if not original_url.lower().startswith("https://"):
            errors.append("user text original_source_url must be a valid HTTPS URL")
        else:
            try:
                normalize_url(original_url)
            except CompilerWorkerV2Error:
                errors.append("user text original_source_url must be a valid HTTPS URL")

    if url_status != "verified":
        errors.append("user text citation requires source.citation_evidence.original_source_url_status=verified")
    return errors


def extract_user_text_origin_hint(canonical_body: str) -> dict[str, str]:
    """Extract an explicit external article citation from pasted user text."""

    match = re.search(
        r"(?:^|[：:\n])\s*([^〈：:，,。！？\r\n]{2,120}?)〈([^〉\r\n]{3,160})〉，刊於《([^》\r\n]{2,120})》，"
        r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日",
        canonical_body,
    )
    if not match:
        return {}
    year, month, day = match.group(4), int(match.group(5)), int(match.group(6))
    return {
        "author": re.sub(r"\s+", " ", match.group(1).strip()),
        "title": match.group(2).strip(),
        "publication": match.group(3).strip(),
        "original_date": f"{year}-{month:02d}-{day:02d}",
    }


def validate_user_text_origin_hint(
    source: dict[str, Any],
    origin_hint: dict[str, str],
) -> list[str]:
    errors: list[str] = []
    citation = source.get("citation_evidence")
    citation = citation if isinstance(citation, dict) else {}
    if str(citation.get("title") or "").strip() != origin_hint["title"]:
        errors.append("user text citation title does not match cited original title")
    if str(citation.get("authors") or "").strip() != origin_hint["author"]:
        errors.append("user text citation authors do not match cited original authors")
    publication = str(citation.get("publication") or "").strip()
    if publication != origin_hint["publication"]:
        errors.append("user text citation publication does not match cited publication")
    original_date = str(citation.get("original_date") or "").strip()
    if not original_date:
        errors.append("user text citation requires source.citation_evidence.original_date")
    elif original_date != origin_hint["original_date"]:
        errors.append("user text citation date does not match cited original date")
    return errors


def infographic_uses_current_prompt_contract(
    infographic: dict[str, Any],
    package: Path,
) -> bool:
    prompt_path = str(infographic.get("locked_prompt_path") or "").strip()
    if not prompt_path:
        return False
    resolved = (package / prompt_path).resolve(strict=False)
    try:
        resolved.relative_to(package.resolve(strict=False))
    except ValueError:
        return False
    return (
        resolved.is_file()
        and INFOGRAPHIC_PROMPT_CONTRACT_VERSION
        in resolved.read_text(encoding="utf-8", errors="replace")
    )


def validate_infographic_receipt(
    infographic: dict[str, Any],
    index: int,
    package: Path,
    *,
    primary_work_title: str | None = None,
    source: dict[str, Any] | None = None,
) -> list[str]:
    errors: list[str] = []
    if (
        infographic.get("provider") != FORMAL_INFOGRAPHIC_PROVIDER
        or infographic.get("model") != FORMAL_INFOGRAPHIC_MODEL
    ):
        errors.append(
            f"infographic {index} provider/model is not "
            f"{FORMAL_INFOGRAPHIC_PROVIDER}/{FORMAL_INFOGRAPHIC_MODEL}"
        )
    if infographic.get("prompt_compiler") != V2_INFOGRAPHIC_PROMPT_COMPILER:
        errors.append(f"infographic {index} missing v2 prompt compiler receipt")
    template_version = infographic.get("template_version")
    if template_version != V2_INFOGRAPHIC_TEMPLATE_VERSION:
        errors.append(f"infographic {index} has unsupported product master receipt")
    if infographic.get("audience") != INFOGRAPHIC_STANDALONE_AUDIENCE:
        errors.append(f"infographic {index} missing independent third-party audience receipt")
    if infographic.get("content_scope") != "source_only":
        errors.append(f"infographic {index} content scope is not source_only")
    if infographic.get("standalone_content_status") != "passed":
        errors.append(f"infographic {index} standalone content check is not passed")
    for field in ("action_identity", "locked_prompt_sha256", "png_sha256"):
        value = str(infographic.get(field) or "")
        if not re.fullmatch(r"[0-9a-f]{64}", value):
            errors.append(f"infographic {index} invalid {field}")
    prompt_path = str(infographic.get("locked_prompt_path") or "").strip()
    if not prompt_path:
        errors.append(f"infographic {index} missing locked prompt path")
    else:
        resolved_prompt = (package / prompt_path).resolve(strict=False)
        try:
            resolved_prompt.relative_to(package.resolve(strict=False))
        except ValueError:
            errors.append(f"infographic {index} locked prompt path escapes package")
        if not resolved_prompt.exists():
            errors.append(f"infographic {index} locked prompt file is missing")
        else:
            locked_prompt = resolved_prompt.read_text(encoding="utf-8").rstrip("\n")
            if sha256_text(locked_prompt) != infographic.get("locked_prompt_sha256"):
                errors.append(f"infographic {index} locked prompt hash mismatch")
            if (
                INFOGRAPHIC_STANDALONE_PROMPT_MARKER not in locked_prompt
                or INFOGRAPHIC_VISIBLE_TEXT_MARKER not in locked_prompt
            ):
                errors.append(f"infographic {index} locked prompt lacks standalone reader content contract")
            if str(template_version) not in locked_prompt:
                errors.append(f"infographic {index} template receipt does not match locked prompt")
            current_contract = prompt_uses_semantic_readback_contract(locked_prompt)
            source = source or {}
            if current_contract and source.get("primary_work_status") not in {"primary", "none"}:
                errors.append(f"infographic {index} missing primary work classification")
            if primary_work_title:
                visible_section = locked_prompt.split(INFOGRAPHIC_VISIBLE_TEXT_MARKER, 1)[-1]
                first_visible_line = visible_section.strip().splitlines()[0] if visible_section.strip() else ""
                if first_visible_line != f"- {primary_work_title}":
                    errors.append(f"infographic {index} does not place primary work title first")
    asset_path = str(infographic.get("asset_path") or "").strip()
    if not asset_path:
        errors.append(f"infographic {index} missing asset_path")
    else:
        resolved_asset = (package / asset_path).resolve(strict=False)
        try:
            resolved_asset.relative_to(package.resolve(strict=False))
        except ValueError:
            errors.append(f"infographic {index} asset_path escapes package")
        if not resolved_asset.exists():
            errors.append(f"infographic {index} asset file is missing")
        else:
            if sha256_bytes(resolved_asset.read_bytes()) != infographic.get("png_sha256"):
                errors.append(f"infographic {index} png hash mismatch")
            if (
                "locked_prompt" in locals()
                and prompt_uses_semantic_readback_contract(locked_prompt)
            ):
                readback = infographic.get("semantic_readback")
                errors.extend(
                    f"infographic {index} {message}"
                    for message in image_reverse_review_errors(
                        readback,
                        allow_historical_spec_receipt=True,
                    )
                )
                errors.extend(
                    f"infographic {index} {message}"
                    for message in image_semantic_readback_errors(
                        readback if isinstance(readback, dict) else None,
                        png_sha256=str(infographic.get("png_sha256") or ""),
                        prompt=locked_prompt,
                    )
                )
            try:
                from PIL import Image

                with Image.open(resolved_asset) as image:
                    dimensions = infographic.get("dimensions")
                    if isinstance(dimensions, dict) and (
                        dimensions.get("width") != image.width or dimensions.get("height") != image.height
                    ):
                        errors.append(f"infographic {index} dimensions readback mismatch")
            except Exception as exc:  # pragma: no cover - image parser failures are surfaced as validation blockers.
                errors.append(f"infographic {index} image readback failed: {exc}")
    dimensions = infographic.get("dimensions")
    if not isinstance(dimensions, dict):
        errors.append(f"infographic {index} missing dimensions readback")
    else:
        width = dimensions.get("width")
        height = dimensions.get("height")
        if (
            not isinstance(width, int)
            or not isinstance(height, int)
            or width <= 0
            or height <= 0
        ):
            errors.append(f"infographic {index} has invalid dimensions readback")
    visual_qc = infographic.get("visual_qc")
    if not isinstance(visual_qc, dict) or visual_qc.get("status") != "ready":
        errors.append(f"infographic {index} visual_qc is not ready")
    elif visual_qc.get("failed_checks"):
        errors.append(f"infographic {index} visual_qc has failed checks")
    if not str(infographic.get("page_number_readback") or "").strip():
        errors.append(f"infographic {index} missing page number readback")
    footer = str(infographic.get("footer_text_readback") or "")
    if "來源:" not in comparable_visible_text(footer) or "資訊圖:" not in comparable_visible_text(footer):
        errors.append(f"infographic {index} footer readback does not match product contract")
    if (
        "locked_prompt" in locals()
        and prompt_uses_semantic_readback_contract(locked_prompt)
    ):
        expected_visible = visible_text_from_prompt(locked_prompt)
        expected_footer = expected_visible[-1] if expected_visible else ""
        expected_page = expected_visible[-2] if len(expected_visible) > 1 else ""
        expected_footer = accepted_footer_text(infographic.get("semantic_readback"), str(infographic.get("png_sha256") or ""), locked_prompt)
        if not footer_format_equivalent(expected_footer, footer):
            errors.append(f"infographic {index} footer readback does not match locked prompt")
        if comparable_visible_text(infographic.get("page_number_readback")) != comparable_visible_text(expected_page):
            errors.append(f"infographic {index} page number readback does not match locked prompt")
    return errors


def markdown_cell(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip()).replace("|", "\\|")


def string_list(value: object) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if str(value or "").strip():
        return [str(value).strip()]
    return []


def render_source_slice(kp: dict[str, Any], package: Path) -> str:
    source = kp["source"]
    asset_mode = str(source.get("asset_mode") or ASSET_MODE_FORMAL)
    source_id = str(source["source_id"])
    title = str(source["title"])
    source_kind = str(source["source_input_kind"])
    analysis = kp.get("analysis") if isinstance(kp.get("analysis"), dict) else {}
    application = kp.get("application") if isinstance(kp.get("application"), dict) else {}
    capture_date = str(source.get("capture_date") or date.today().isoformat())
    retention_mode = str(source.get("retention_mode") or source.get("source_retention_mode") or "summarized_allowed")
    identity_rows = [
        f"| 來源輸入類型 | {markdown_cell(source_kind)} |",
        f"| 來源身分 | {markdown_cell(source['source_identity'])} |",
        f"| 資產模式 | {markdown_cell(asset_mode)} |",
    ]
    if source_kind == "url":
        identity_rows.extend([
            f"| URL | {markdown_cell(source.get('source_url') or source.get('normalized_url'))} |",
            "| URL 綁定狀態 | verified |",
        ])
    elif source_kind == "user_provided_text":
        identity_rows.append(f"| 來源內容 SHA-256 | {markdown_cell(source.get('source_content_sha256'))} |")
        if str(source.get("content_owner") or "").strip():
            identity_rows.append(f"| 內容歸屬 | {markdown_cell(source.get('content_owner'))} |")
        citation = source.get("citation_evidence")
        citation = citation if isinstance(citation, dict) else {}
        if str(citation.get("title") or "").strip():
            identity_rows.append(f"| 原文題名 | {markdown_cell(citation.get('title'))} |")
        if str(citation.get("publication") or "").strip():
            identity_rows.append(f"| 原始來源機構 | {markdown_cell(citation.get('publication'))} |")
        if str(citation.get("authors") or "").strip():
            identity_rows.append(f"| 原作者 | {markdown_cell(citation.get('authors'))} |")
        if str(citation.get("original_date") or "").strip():
            identity_rows.append(f"| 原文日期 | {markdown_cell(citation.get('original_date'))} |")
        if str(citation.get("original_source_url") or "").strip():
            identity_rows.extend([
                f"| 原文 URL | {markdown_cell(citation.get('original_source_url'))} |",
                f"| 原文 URL 核對狀態 | {markdown_cell(citation.get('original_source_url_status'))} |",
            ])
    else:
        review = read_json(package / "review_packet.json")
        dimensions = source.get("source_image_dimensions") or {}
        original_path = str(review.get("original_image_path") or "")
        identity_rows.extend([
            f"| 原始圖片檔 | {markdown_cell(original_path)} |",
            f"| 來源圖片 SHA-256 | {markdown_cell(source.get('source_image_sha256'))} |",
            f"| 圖片 MIME | {markdown_cell(source.get('source_image_mime'))} |",
            f"| 圖片尺寸 | {dimensions.get('width', 0)} × {dimensions.get('height', 0)} |",
            "| 正文保存模式 | original_image_required |",
        ])
    if asset_mode == ASSET_MODE_CASE:
        evidence = kp.get("case_evidence") if isinstance(kp.get("case_evidence"), dict) else {}
        identity_rows.extend([
            f"| Evidence manifest | {markdown_cell(evidence.get('manifest_path'))} |",
            f"| Manifest SHA-256 | {markdown_cell(evidence.get('manifest_sha256'))} |",
            f"| Source manifest SHA-256 | {markdown_cell(source.get('case_evidence_source_manifest_sha256'))} |",
        ])

    claims = analysis.get("source_claims") if isinstance(analysis.get("source_claims"), list) else []
    if not claims:
        claims = [
            {"claim": item.get("text", ""), "evidence_anchor": item.get("source_evidence", item.get("text", ""))}
            for item in kp.get("coverage_units", [])
            if isinstance(item, dict)
        ]
    claim_lines = [
        f"{index}. {markdown_cell(item.get('claim') or item.get('text'))}"
        for index, item in enumerate(claims, start=1)
        if isinstance(item, dict) and (item.get("claim") or item.get("text"))
    ]
    summary_items = string_list(analysis.get("summary"))
    if not summary_items:
        summary_items = [str(kp["knowledge"][0].get("claim") or kp["knowledge"][0].get("title") or title)]
    tldr_lines = [f"{index}. {item}" for index, item in enumerate(summary_items, start=1)]

    coverage_rows = []
    coverage_lookup: dict[str, str] = {}
    for claim in claims:
        if isinstance(claim, dict) and claim.get("id"):
            coverage_lookup[str(claim["id"])] = str(
                claim.get("evidence_anchor") or claim.get("claim") or ""
            )
    for unit in kp.get("coverage_units", []):
        if not isinstance(unit, dict):
            continue
        unit_id = str(unit.get("id") or "")
        unit_text = str(unit.get("text") or "")
        coverage_lookup[unit_id] = str(unit.get("source_evidence") or unit_text)
        coverage_rows.append(
            "| {id} | {text} | {types} | {axes} | {dispositions} |".format(
                id=markdown_cell(unit_id),
                text=markdown_cell(unit_text),
                types=markdown_cell("；".join(string_list(unit.get("unit_types")))),
                axes=markdown_cell("；".join(string_list(unit.get("value_axes")))),
                dispositions=markdown_cell("；".join(sorted(coverage_unit_dispositions(unit)))),
            )
        )

    coverage_audit = build_coverage_audit(kp)
    disposition_counts = coverage_audit["disposition_counts"]
    assert isinstance(disposition_counts, dict)
    omitted_units = coverage_audit["omitted_units"]
    assert isinstance(omitted_units, list)
    summary_only_units = string_list(coverage_audit.get("summary_only_units"))
    coverage_audit_rows = [
        f"| 覆蓋狀態 | {markdown_cell(coverage_audit.get('status'))} |",
        f"| 重要單元總數 | {coverage_audit.get('total_units')} |",
        f"| 已映射入資訊圖 | {coverage_audit.get('mapped_to_infographic')} |",
        f"| 正式資訊圖頁數 | {coverage_audit.get('infographic_page_count')} |",
        f"| 多卷狀態 | {markdown_cell(coverage_audit.get('multi_volume_status'))} |",
        f"| 來源主張單元 | {disposition_counts.get('source_claim', 0)} |",
        f"| 主知識點單元 | {disposition_counts.get('knowledge_point', 0)} |",
        f"| 應用單元 | {disposition_counts.get('application', 0)} |",
        f"| 關係單元 | {disposition_counts.get('relation', 0)} |",
        f"| 明確省略項 | {len(omitted_units)} |",
        f"| 只作摘要項 | {len(summary_only_units)} |",
        f"| 漏入圖項 | {markdown_cell('、'.join(string_list(coverage_audit.get('missing_infographic_mappings'))) or '無')} |",
        f"| 重複入圖項 | {markdown_cell('、'.join(string_list(coverage_audit.get('duplicate_infographic_mappings'))) or '無')} |",
        f"| 未知入圖項 | {markdown_cell('、'.join(string_list(coverage_audit.get('unknown_infographic_mappings'))) or '無')} |",
    ]
    coverage_page_rows = [
        "| {page} | {page_id} | {title} | {coverage_ids} | {visible_count} |".format(
            page=page.get("page"),
            page_id=markdown_cell(page.get("page_id")),
            title=markdown_cell(page.get("title")),
            coverage_ids=markdown_cell("、".join(string_list(page.get("coverage_ids")))),
            visible_count=page.get("visible_text_count"),
        )
        for page in coverage_audit.get("pages") or []
        if isinstance(page, dict)
    ]
    if asset_mode == ASSET_MODE_CASE:
        evidence = kp.get("case_evidence") if isinstance(kp.get("case_evidence"), dict) else {}
        coverage_audit_rows = [
            "| 覆蓋狀態 | case_evidence：完整來源與方法卡保留，非正式資訊圖模式 |",
            f"| 案例證據 manifest | {markdown_cell(evidence.get('manifest_path'))} |",
            f"| 案例證據 SHA-256 | {markdown_cell(evidence.get('manifest_sha256'))} |",
            f"| 重要單元總數 | {coverage_audit.get('total_units')} |",
            "| 正式資訊圖頁數 | 不適用（case_evidence） |",
            "| 來源截圖／本地重現 | 見下方案例證據，原站與衍生成品分列 |",
        ]
        coverage_page_rows = ["| case-evidence | case_evidence | 原站證據與本地最小重現 | 不適用 | 不適用 |"]
    omission_rows = [
        "| {id} | {reason} |".format(
            id=markdown_cell(item.get("id")),
            reason=markdown_cell(item.get("reason") or "未記錄"),
        )
        for item in omitted_units
        if isinstance(item, dict)
    ]

    kp_rows = []
    for item in kp.get("knowledge", []):
        if not isinstance(item, dict):
            continue
        kp_rows.append(
            "| {id} | {claim} | {layer} | {confidence} | {evidence} |".format(
                id=markdown_cell(item.get("kp_id")),
                claim=markdown_cell(item.get("claim") or item.get("title")),
                layer=markdown_cell(item.get("layer") or "原文主張"),
                confidence=markdown_cell(item.get("confidence") or "中"),
                evidence=markdown_cell(item.get("evidence_anchor") or item.get("source_evidence")),
            )
        )
    relation_rows = []
    for item in kp.get("relations") or []:
        if not isinstance(item, dict):
            continue
        relation_rows.append(
            "| {source} | {target} | {relation} | {evidence} | {confidence} |".format(
                source=markdown_cell(item.get("source_kp") or item.get("source")),
                target=markdown_cell(item.get("target_kp") or item.get("target")),
                relation=markdown_cell(item.get("relation") or item.get("type")),
                evidence=markdown_cell(item.get("evidence_anchor") or item.get("evidence")),
                confidence=markdown_cell(item.get("confidence") or "中"),
            )
        )

    full_body = ""
    full_body_rows: list[str] = []
    if source_kind in {"url", "user_provided_text"} and retention_mode == "full_body_required":
        canonical_body = (package / "canonical_source.md").read_text(encoding="utf-8").rstrip("\n")
        raw_anchors = string_list(source.get("anchors"))
        anchors = [
            anchor
            for anchor in raw_anchors
            if not anchor.lstrip().startswith("#") and ";" not in anchor
        ][:8]
        if not anchors:
            anchors = [
                line.strip()
                for line in canonical_body.splitlines()
                if line.strip() and not line.lstrip().startswith("#") and ";" not in line
            ][:8]
        canonical_source_label = (
            "Compiler Worker v2 canonical source"
            if source_kind == "url"
            else "Compiler Worker v2 user-provided canonical text"
        )
        # The canonical article can itself contain fenced code blocks.  Keep the
        # outer preservation fence strictly longer than every backtick run in
        # the article, so the vault reader never mistakes an inner code fence
        # for the end of the full-body record.
        longest_inner_fence = max(
            (len(match.group(0)) for match in re.finditer(r"`+", canonical_body)),
            default=0,
        )
        canonical_fence = "`" * max(3, longest_inner_fence + 1)
        full_body_rows.extend([
            "| 正文保存模式 | full_body_required |",
            f"| 正文保存 SHA-256 | {source['canonical_body_sha256']} |",
            f"| 正文代表錨點 | {markdown_cell(' / '.join(anchors))} |",
        ])
        full_body = "\n".join([
            "## 正文保存區",
            "",
            "| 欄位 | 內容 |",
            "|---|---|",
            "| 保存模式 | full_body_required |",
            f"| 正文 SHA-256 | {source['canonical_body_sha256']} |",
            f"| 正文來源 | {canonical_source_label} |",
            "| 正文清理邊界 | 只保存已核對的來源正文 |",
            f"| 代表錨點 | {markdown_cell(' / '.join(anchors))} |",
            "",
            "### 正文代表錨點",
            "",
            *[f"- {anchor}" for anchor in anchors],
            "",
            "### Canonical article body",
            "",
            f"{canonical_fence}markdown",
            canonical_body,
            canonical_fence,
            "",
        ])

    personalization_links = string_list(application.get("personalization_links"))
    compounding_types = string_list(application.get("compounding_insight_types"))
    infographic_lines: list[str] = ["## 資訊圖", ""]
    infographics = [item for item in kp.get("infographics") or [] if isinstance(item, dict)]
    total_infographics = len(infographics)
    page_divisions = [
        str(item.get("subtitle") or item.get("page_focus") or item.get("title") or "").strip()
        for item in infographics
    ]
    page_count_decision = (
        f"共 {total_infographics} 頁；"
        + "；".join(
            f"第 {page_index} 頁處理「{division}」"
            for page_index, division in enumerate(page_divisions, start=1)
            if division
        )
    )
    for index, infographic in enumerate(infographics, start=1):
        asset_path = str(infographic.get("asset_path") or "")
        anchors = [coverage_lookup.get(item, item) for item in string_list(infographic.get("coverage_ids"))]
        visible_text = string_list(infographic.get("visible_text"))
        page_division = str(
            infographic.get("subtitle")
            or infographic.get("page_focus")
            or "由本頁 coverage_ids 決定的獨立理解任務。"
        ).strip()
        page_record_title = (
            f"{infographic.get('title') or title}｜{page_division}"
            if total_infographics > 1
            else str(infographic.get("title") or title)
        )
        infographic_lines.extend([
            f"![{markdown_cell(page_record_title)}]({asset_path})",
            "",
            f"### 資訊圖 {index}:{markdown_cell(page_record_title)}",
            "",
            f"- 標題:{markdown_cell(page_record_title)}",
            "- 原文實體錨點:",
            *[f"  - {markdown_cell(anchor)}" for anchor in anchors[:8]],
            "- 圖內文字:",
            *[f"  {number}. {markdown_cell(line)}" for number, line in enumerate(visible_text, start=1)],
            "- coverage_ids_json:" + json.dumps(string_list(infographic.get("coverage_ids")), ensure_ascii=True).replace("|", "\\u007c"),
            f"- 重要性覆蓋:本頁覆蓋 {markdown_cell('、'.join(string_list(infographic.get('coverage_ids'))))}，其餘細節保留於來源筆記。",
            f"- 本圖分工:{markdown_cell(page_division + ('；本頁回答：' + str(infographic['reader_question']).strip() if str(infographic.get('reader_question') or '').strip() else ''))}",
            f"- 本 URL 資訊圖數量判斷:{markdown_cell(page_count_decision)}",
            f"- 承接邏輯:{markdown_cell(infographic.get('page_focus') or '由來源證據連到讀者可見重點。')}",
            f"- 連線 / 分組理由:{markdown_cell((infographic.get('reader_review') or {}).get('relationship_explanation') or '歷史版本未保存獨立脈絡解釋；不可推論冷讀通過。')}",
            f"- 斷橋檢查:{markdown_cell((infographic.get('reader_review') or {}).get('verdict') or '歷史版本未核實')}",
            f"- 正文敘事充足性檢查:{markdown_cell((infographic.get('reader_review') or {}).get('answer') or '歷史版本未保存冷讀回答。')}",
            "- PNG 生成方式:Codex 內建 image_gen（gpt-image-2）直接生成 PNG",
            "- PNG 語義驗收狀態:passed",
            "- PNG 語義驗收證據:已驗證鎖定提示、圖片雜湊、尺寸、頁碼、頁尾及獨立讀者內容邊界。",
            "- PNG 反審狀態:passed",
            f"- PNG 反審證據:{infographic_reverse_review_evidence_line()}",
            f"- 語義驗收:{markdown_cell((infographic.get('semantic_readback') or {}).get('reader_review') or '尚無本頁實際圖片讀回；不宣稱通過。')}",
            f"- 讀者判斷句:{markdown_cell(infographic.get('page_focus') or summary_items[0])}",
            "",
        ])
    asset_lines = infographic_lines
    if asset_mode == ASSET_MODE_CASE:
        evidence = kp.get("case_evidence") if isinstance(kp.get("case_evidence"), dict) else {}
        creator = evidence.get("creator") if isinstance(evidence.get("creator"), dict) else {}
        method_card = evidence.get("method_card") if isinstance(evidence.get("method_card"), dict) else {}
        reproduction = evidence.get("reproduction") if isinstance(evidence.get("reproduction"), dict) else {}
        asset_lines = [
            "## 案例證據",
            "",
            "| 欄位 | 內容 |",
            "|---|---|",
            f"| 主要創作者 | {markdown_cell(creator.get('name'))} |",
            f"| 文章日期 | {markdown_cell(creator.get('article_date'))} |",
            f"| AI 參與原文依據 | {markdown_cell(creator.get('ai_evidence_quote'))} |",
            f"| Evidence manifest | {markdown_cell(evidence.get('manifest_path'))} |",
            f"| Manifest SHA-256 | {markdown_cell(evidence.get('manifest_sha256'))} |",
            "",
            "### 方法卡",
            "",
            f"- 名稱：{markdown_cell(method_card.get('title'))}",
            f"- 用途：{markdown_cell(method_card.get('purpose'))}",
            f"- 觀察到的設計：{markdown_cell(method_card.get('observed_design'))}",
            f"- 最小重現方法：{markdown_cell(method_card.get('reproduction_method'))}",
            f"- 適用邊界：{markdown_cell(method_card.get('boundary'))}",
            "",
            *([str(method_card["learning_notes"]).strip(), ""]
              if isinstance(method_card.get("learning_notes"), str) and method_card["learning_notes"].strip()
              else []),
            "### 真實成品截圖",
            "",
        ]
        for screenshot in evidence.get("screenshots") or []:
            if not isinstance(screenshot, dict):
                continue
            role = markdown_cell(screenshot.get("role"))
            viewport = screenshot.get("viewport") if isinstance(screenshot.get("viewport"), dict) else {}
            asset_lines.extend([
                f"![原站截圖：{role}]({screenshot.get('path')})",
                "",
                f"- 類型：{role}；viewport：{viewport.get('width')} × {viewport.get('height')}；操作：{markdown_cell('；'.join(string_list(screenshot.get('actions'))))}",
                f"- 真實閱圖讀回：{markdown_cell(screenshot.get('visual_readback'))}",
                "",
            ])
        asset_lines.extend([
            "### 本地最小重現",
            "",
            f"- 可執行入口：[{markdown_cell(reproduction.get('entrypoint'))}]({reproduction.get('entrypoint')})",
            f"- 依賴快照：[{markdown_cell(reproduction.get('dependency_snapshot_path'))}]({reproduction.get('dependency_snapshot_path')})",
            f"- 實際執行輸出：[readback]({reproduction.get('runtime_output_path')})",
            f"- 本機執行：`{markdown_cell(' '.join((reproduction.get('runner') or {}).get('argv') or []))}`（cwd：{markdown_cell((reproduction.get('runner') or {}).get('cwd'))}）",
            f"- 瀏覽器操作：{markdown_cell(' → '.join(reproduction.get('browser_actions') or []))}（對照 action：{markdown_cell(reproduction.get('comparison_action_id'))}）",
            f"- 視覺／操作讀回：{markdown_cell(reproduction.get('visual_readback'))}",
            f"![本地重現對照]({reproduction.get('comparison_screenshot_path')})",
            "",
            "### 技術參照（只支援本地重現，不併入原文）",
            "",
        ])
        for reference in evidence.get("technical_references") or []:
            if not isinstance(reference, dict):
                continue
            asset_lines.append(
                f"- [{markdown_cell(reference.get('url'))}]({reference.get('snapshot_path')})：版本 {markdown_cell(reference.get('version'))}；用途 {markdown_cell(reference.get('purpose'))}；界線 {markdown_cell(reference.get('boundary'))}"
            )
        asset_lines.append("")

    allowed_source_types = {
        "Facebook",
        "Twitter/X",
        "使用者提供圖片",
        "使用者提供文字",
        "公司官網",
        "其他",
        "官方文件",
        "影片",
        "新聞",
        "部落格",
    }
    default_source_type = {
        "url": "官方文件",
        "user_provided_text": "使用者提供文字",
        "user_provided_image": "使用者提供圖片",
    }[source_kind]
    source_type = str(source.get("source_type") or default_source_type)
    if source_type not in allowed_source_types:
        source_type = default_source_type
    allowed_knowledge_uses = {"事實真源", "判斷材料", "品味材料", "噪音暫存", "學習材料", "工作流程", "行動材料"}
    knowledge_use = str(application.get("knowledge_use") or "事實真源")
    if knowledge_use not in allowed_knowledge_uses:
        knowledge_use = "事實真源"
    readability = str(source.get("readability_status") or "public_ok")
    taste_status = str(application.get("taste_network_status") or application.get("taste_link_status") or "否")
    taste_reason = str(application.get("taste_reason") or "本來源未提供品味探索判斷。")
    adoption_status = str(application.get("adoption_status") or "待判斷")
    adoption_reason = str(application.get("adoption_reason") or "依來源內容及知識點判斷。")
    compounding_status = str(application.get("compounding_insight_status") or "no_insight")
    compounding_next = str(application.get("compounding_next_action") or "未有後續行動。")
    judgments = application.get("compounding_judgments")
    if isinstance(judgments, dict):
        compounding_rows = [f"- {kind}:{row['decision']}；{markdown_cell(row['reason'])}" for kind, row in judgments.items()]
    else:
        compounding_rows = [
            f"- {kind}:" + (
                "成立；" + (" / ".join(personalization_links) or compounding_next) if kind in compounding_types
                else "需要更多脈絡；尚未核對相關既有筆記。" if compounding_status == "needs_context"
                else "不成立；本次未有足夠證據建立這類扣連。"
            ) for kind in ("補底", "加深", "連接", "修正", "行動")
        ]
    learning_title = markdown_cell(title)
    primary_work_rows = []
    primary_work_status = str(source.get("primary_work_status") or "").strip()
    if primary_work_status:
        primary_work_rows.append(
            f"| 主要作品判定 | {markdown_cell(primary_work_status)} |"
        )
    if str(source.get("primary_work_title") or "").strip():
        primary_work_rows.append(
            f"| 主要作品標題 | {markdown_cell(source.get('primary_work_title'))} |"
        )
    if str(source.get("primary_work_evidence") or "").strip():
        primary_work_rows.append(
            f"| 主要作品證據 | {markdown_cell(source.get('primary_work_evidence'))} |"
        )
    lines = [
        f"# {source_id} - {title}",
        "",
        "## 來源稽核",
        "",
        "| 欄位 | 內容 |",
        "|---|---|",
        *identity_rows,
        f"| 來源名稱 | {markdown_cell(title)} |",
        *primary_work_rows,
        f"| 來源類型 | {markdown_cell(source_type)} |",
        f"| 擷取日期 | {capture_date} |",
        f"| 可讀性狀態 | {markdown_cell(readability)} |",
        *full_body_rows,
        "| 是否可入庫 | 可以 |",
        "| AI 來源分析狀態 | complete |",
        "| AI 來源分析證據 | Knowledge Package 的來源、覆蓋、知識點與圖片憑證已通過一致性檢查。 |",
        "## 原文層",
        "",
        "### 原文主張",
        "",
        *(claim_lines or [f"1. {summary_items[0]}"]),
        "",
        "## TL;DR",
        "",
        *tldr_lines,
        "",
        full_body,
        "## 知識用途分流",
        "",
        "| 類型 | 內容 |",
        "|---|---|",
        f"| 主要用途 | {markdown_cell(knowledge_use)} |",
        f"| 是否入品味探索網 | {markdown_cell(taste_status)} |",
        f"| 不入或入品味網原因 | {markdown_cell(taste_reason)} |",
        "",
        "### 內容價值覆蓋",
        "",
        "| ID | 內容 | 單元類型 | 價值軸 | 處置 |",
        "|---|---|---|---|---|",
        *coverage_rows,
        "",
        "### 知識點草案",
        "",
        "| ID | 知識點 | 層級 | 可信度 | 來源證據 |",
        "|---|---|---|---|---|",
        *kp_rows,
        "",
        "### 關係草案",
        "",
        "| 來源知識點 | 目標知識點 | 關係 | 證據錨點 | 信心 |",
        "|---|---|---|---|---|",
        *relation_rows,
        "",
        "## 覆蓋審計",
        "",
        "| 欄位 | 內容 |",
        "|---|---|",
        *coverage_audit_rows,
        "",
        "### 覆蓋分頁",
        "",
        "| 頁 | Page ID | 標題 | 覆蓋 ID | 可見文字數 |",
        "|---|---|---|---|---|",
        *(coverage_page_rows or ["| 0 | missing | missing | missing | 0 |"]),
        "",
        "### 明確省略",
        "",
        "| 覆蓋 ID | 省略理由 |",
        "|---|---|",
        *(omission_rows or ["| 無 | 無 |"]),
        "",
        "## For 使用者:個人化消化",
        "",
        "### 關係",
        "",
        *(personalization_links or ["本來源暫未建立個人化扣連。"]),
        "",
        "### 既有知識扣連",
        "",
        *([f"{index}. {item}" for index, item in enumerate(personalization_links, start=1)] or ["1. 暫未建立既有知識扣連。"]),
        "",
        "### 採納判斷",
        "",
        f"- 狀態:{adoption_status}",
        f"- 一句原因:{adoption_reason}",
        "",
        "### 品味扣連",
        "",
        f"- 狀態:{application.get('taste_link_status') or 'not_applicable'}",
        f"- 一句原因:{taste_reason}",
        "",
        "### 洞見",
        "",
        summary_items[0],
        "",
        "### 跨來源洞見判斷",
        "",
        f"- 狀態:{compounding_status}",
        f"- 類型:{' / '.join(compounding_types) or '未成立'}",
        f"- 扣連:{' / '.join(personalization_links) or '未建立'}",
        f"- 下一步:{compounding_next}",
        *compounding_rows,
        "",
        "### 應用場景",
        "",
        f"1. {compounding_next}",
        "",
        *render_application_enrichment(application, package),
        *asset_lines,
        "## 學習檢查",
        "",
        "| 題型 | 問題 | 答案標準 |",
        "|---|---|---|",
        f"| 理解題 | {learning_title} 的核心主旨是甚麼？ | 能說出 TL;DR 的主要判斷。 |",
        "| 保持題 | 哪個來源證據支持主要知識點？ | 能指出至少一個來源錨點。 |",
        "| 遷移題 | 這些知識可如何用於另一個情境？ | 能提出一項不超出來源證據的應用。 |",
        "| 應用任務 | 選一個知識點寫出下一步。 | 行動須能追溯至來源或應用判斷。 |",
        "",
    ]
    return "\n".join(str(line) for line in lines if line is not None)


def application_enrichment_errors(application, package):
    errors = []
    body = application.get("analysis_markdown")
    if body is not None and (not isinstance(body, str) or not body.strip()):
        errors.append("application analysis_markdown must be nonempty Markdown")
    seen = set()
    for item in application.get("supporting_sources", []):
        if not isinstance(item, dict):
            errors.append("supporting source must be an object"); continue
        url = str(item.get("url") or "")
        if not url.startswith("https://") or url in seen:
            errors.append("supporting sources require distinct HTTPS URLs")
        seen.add(url)
        if item.get("source_identity") != "url:" + sha256_text(normalize_url(url)):
            errors.append("supporting source identity does not match its own URL")
        if not item.get("title") or not item.get("supporting_sections"):
            errors.append("supporting source title and supporting sections are required")
        try:
            path = package_relative_path(package, item.get("capture_path", ""), "supporting source capture")
            if not path.is_file() or sha256_bytes(path.read_bytes()) != item.get("capture_sha256"):
                errors.append("supporting source capture hash mismatch")
        except (CompilerWorkerV2Error, ValueError):
            errors.append("supporting source capture must stay inside the package")
    ids = set()
    for item in application.get("related_sources", []):
        if (not isinstance(item, dict) or not re.fullmatch(r"KS-VS-[A-Z0-9-]+", str(item.get("source_id") or ""))
                or item.get("source_id") in ids or not str(item.get("note") or "").strip()):
            errors.append("related sources require unique source IDs and a relationship note")
        elif isinstance(item, dict):
            ids.add(item["source_id"])
    return errors


def amend_application(root, kp_path, response_path):
    """Amend only the application before the writer; retain source, images and timing.

    The bound journal resumes either half of the two-file update. It never resets
    a source, image attempt, locked image plan or an already-started writer.
    """
    import copy
    kp_path = relative_to_root(kp_path, root, "Knowledge Package")
    package = kp_path.parent
    response = read_json(response_path)
    response_hash = canonical_payload_sha256(response)
    journal_path = package / "application_amendment.json"
    kp = read_json(kp_path)
    job = read_json(job_root(root) / (str(kp["job_id"]) + ".json"))
    if (package / "completion_receipt.json").exists() or job.get("status") in {"committing", "completed"} or any(k.startswith("commit_") for k in job):
        raise CompilerWorkerV2Error("application amendment must precede every writer checkpoint")
    if journal_path.exists():
        journal = read_json(journal_path)
        if journal.get("payload_sha256") != canonical_payload_sha256(journal) or journal.get("response_sha256") != response_hash:
            raise CompilerWorkerV2Error("application amendment journal conflicts with this request")
        if (journal.get("state") == "completed" and kp.get("application") == journal["after_kp"]["application"]
                and kp.get("analysis") == journal["after_kp"].get("analysis")):
            require_ingest_plan(job, kp)
            return {"status": "application_updated", "write_performed": False, "job_id": job["job_id"]}
        if canonical_payload_sha256(kp) not in {journal["before_kp_sha256"], canonical_payload_sha256(journal["after_kp"])} or canonical_payload_sha256(job) not in {journal["before_job_sha256"], canonical_payload_sha256(journal["after_job"])}:
            # A completed amendment can be read again after ordinary image progress.
            if (journal.get("state") == "completed" and kp.get("application") == journal["after_kp"]["application"]
                    and kp.get("analysis") == journal["after_kp"].get("analysis")):
                require_ingest_plan(job, kp)
                return {"status": "application_updated", "write_performed": False, "job_id": job["job_id"]}
            raise CompilerWorkerV2Error("application amendment encountered unrelated concurrent changes")
    else:
        validate_job_package_binding(root, kp_path, kp)
        require_ingest_plan(job, kp)
        if (response.get("schema_version") != "application_amendment_v1"
                or response.get("job_id") != job["job_id"]
                or response.get("previous_scope_sha256") != job["execution_contract"]["scope_sha256"]
                or len(str(response.get("user_request") or "").strip()) < 6
                or not isinstance(response.get("application"), dict)):
            raise CompilerWorkerV2Error("application amendment requires the same job, locked scope and user request")
        candidate = copy.deepcopy(kp)
        candidate["application"] = response["application"]
        if "analysis" in response:
            if not isinstance(response["analysis"], dict) or not set(kp.get("analysis", {})) <= set(response["analysis"]):
                raise CompilerWorkerV2Error("analysis amendment must retain the complete existing analysis fields")
            candidate["analysis"] = response["analysis"]
        from ingest_plan_contract import content_errors, schema_errors, plan_schema
        schema = plan_schema()["properties"]
        shape_errors = schema_errors(candidate["application"], schema["application"])
        if "analysis" in response:
            shape_errors += schema_errors(candidate["analysis"], schema["analysis"])
        if shape_errors:
            raise CompilerWorkerV2Error("; ".join(shape_errors))
        errors = content_errors(candidate, package, root=root) + application_enrichment_errors(candidate["application"], package)
        if errors:
            raise CompilerWorkerV2Error("; ".join(errors))
        # Retain every prior field unless the submitted full application explicitly changes it.
        if not set(kp["application"]) <= set(candidate["application"]):
            raise CompilerWorkerV2Error("application amendment cannot omit existing application fields")
        scope = ingest_plan_scope(job, candidate)
        old_scope = job["execution_contract"]["scope"]
        if any(scope.get(k) != v for k, v in old_scope.items() if k != "semantic_content_sha256"):
            raise CompilerWorkerV2Error("application amendment cannot change the source, coverage or images")
        next_job = copy.deepcopy(job)
        next_job["execution_contract"].update(scope=scope, scope_sha256=canonical_payload_sha256(scope))
        next_job["application_amendment"] = {"previous_scope_sha256": response["previous_scope_sha256"], "response_sha256": response_hash}
        journal = {"schema_version": "application_amendment_journal_v1", "state": "prepared", "job_id": job["job_id"],
                   "response_sha256": response_hash, "before_kp_sha256": canonical_payload_sha256(kp),
                   "before_job_sha256": canonical_payload_sha256(job), "before_application": kp["application"],
                   "before_execution_contract": job["execution_contract"], "after_kp": candidate, "after_job": next_job}
        journal["payload_sha256"] = canonical_payload_sha256(journal)
        write_json(journal_path, journal)
    write_json(kp_path, journal["after_kp"])
    write_v2_job(root, journal["after_job"])
    require_ingest_plan(read_json(job_root(root) / (job["job_id"] + ".json")), read_json(kp_path))
    journal["state"] = "completed"
    journal["payload_sha256"] = canonical_payload_sha256(journal)
    write_json(journal_path, journal)
    return {"status": "application_updated", "job_id": job["job_id"], "write_performed": True,
            "source_and_image_attempts_unchanged": True, "next": "continue the existing ingest rail"}


def render_application_enrichment(application, package):
    errors = application_enrichment_errors(application, package)
    if errors:
        raise CompilerWorkerV2Error("; ".join(errors))
    body = application.get("analysis_markdown")
    if not body:
        return []
    # This section stays within the existing application section in the source slice.
    body = re.sub(r"^(#{1,6})[ \t]+", lambda m: "#" * min(6, len(m[1]) + 2) + " ", body, flags=re.M)
    refs = application.get("supporting_sources", [])
    related = application.get("related_sources", [])
    lines = ["### 深入解讀", "", body, "", "### 補充來源身分", ""]
    for item in refs:
        lines.append(f"- [{item['title']}]({item['url']})：支持章節 {item['supporting_sections']}；獨立身分 `{item['source_identity']}`；保存正文 SHA-256 `{item['capture_sha256']}`。")
    if related:
        lines.extend(["", "<!-- related_sources_json: " + json.dumps(related, ensure_ascii=False) + " -->", ""])
    return lines


def project_source_slice(kp: dict[str, Any], package: Path) -> Path:
    source = kp["source"]
    source_id = str(source["source_id"])
    prefix = f"{source_id}_"
    revision = kp.get("revision") if isinstance(kp.get("revision"), dict) else {}
    slug = str(revision.get("source_slug") or "").strip()
    if not slug:
        slug = (
            package.name[len(prefix):]
            if package.name.startswith(prefix)
            else slugify(str(source["title"]))
        )
    target = package / f"{source_id}_{slug}.md"
    try:
        assert_generated_path(target, label="Compiler Worker source slice")
    except PathBudgetError as exc:
        raise CompilerWorkerV2Error(str(exc)) from exc
    rendered = render_source_slice(kp, package).rstrip() + "\n"
    target.write_text(rendered, encoding="utf-8")
    readback = target.read_text(encoding="utf-8")
    if readback != rendered:
        raise CompilerWorkerV2Error("deterministic source slice readback mismatch")
    return target


def run_json_subprocess(command: list[str], cwd: Path, label: str) -> dict[str, Any]:
    completed = subprocess.run(
        command,
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise CompilerWorkerV2Error(
            f"{label} returned invalid JSON: {completed.stderr.strip() or completed.stdout.strip()}"
        ) from exc
    if completed.returncode != 0:
        reason = (
            payload.get("message")
            or payload.get("issues")
            or payload.get("reason")
            or payload.get("write_block_reason")
            or json.dumps(payload, ensure_ascii=False)
        )
        raise CompilerWorkerV2Error(f"{label} failed: {reason}")
    return payload


def write_vault_and_readback(
    *,
    root: Path,
    vault: Path,
    source_slice: Path,
    knowledge_point_count: int,
    write: bool = True,
) -> dict[str, Any]:
    adapter = Path(__file__).resolve().with_name("obsidian_vault_adapter.py")
    command = [
        sys.executable,
        str(adapter),
        "--source-slice",
        str(source_slice),
        "--vault",
        str(vault),
        "--max-kp",
        str(max(1, knowledge_point_count)),
        "--verify",
        "--source-local",
        "--json",
    ]
    if write:
        command.insert(-3, "--write")
    result = run_json_subprocess(command, root, "Obsidian writer" if write else "Obsidian readback")
    verification = result.get("verification") if isinstance(result.get("verification"), dict) else {}
    if result.get("status") != "ready" or not verification.get("passed"):
        failed = sorted(
            key for key, value in verification.items()
            if key != "passed" and value is False
        )
        raise CompilerWorkerV2Error(
            "Obsidian writer did not pass source-local readback"
            + (f": {', '.join(failed)}" if failed else "")
        )
    return result


def generate_incremental_outcome(root: Path, vault: Path, source_id: str) -> dict[str, Any]:
    generator = Path(__file__).resolve().with_name("outcome_page_generator.py")
    result = run_json_subprocess([
        sys.executable,
        str(generator),
        "--incremental-source-id",
        source_id,
        "--vault",
        str(vault),
        "--output-dir",
        str(vault / "outcome-pages"),
        "--portal",
        str(vault / "成果頁總覽.md"),
        "--json",
    ], root, "outcome generator")
    if result.get("status") != "ready" or not result.get("current_page"):
        raise CompilerWorkerV2Error("incremental outcome generation did not complete")
    return result


def audit_outcome_reader_quality(
    root: Path,
    vault: Path,
    source_id: str,
    *,
    pending_revalidation_revision: dict[str, Any] | None = None,
) -> dict[str, Any]:
    auditor = Path(__file__).resolve().with_name("outcome_page_quality_audit.py")
    audit = run_json_subprocess([
        sys.executable,
        str(auditor),
        "--vault",
        str(vault),
        "--source-id",
        source_id,
        "--json",
    ], root, "outcome reader-quality audit")
    summary = reader_quality_summary(audit, source_id, root)
    expected_pending_issue = f"{source_id} research method is marked needs_revalidation"
    current_page = next(
        (
            page for page in audit.get("pages", [])
            if isinstance(page, dict) and str(page.get("path") or "").endswith(f"{source_id}_outcome.html")
        ),
        {},
    )
    only_pending_revalidation = (
        isinstance(pending_revalidation_revision, dict)
        and pending_revalidation_revision.get("kind") == "case_revalidation"
        and audit.get("status") == "needs_review"
        and audit.get("needs_review_count") == 1
        and not audit.get("blocked_reasons")
        and isinstance(current_page, dict)
        and current_page.get("issue_count") == 1
        and current_page.get("method_verification_issues") == [expected_pending_issue]
        and (audit.get("index") or {}).get("status") == "ready"
        and (audit.get("portal") or {}).get("status") == "ready"
    )
    if only_pending_revalidation:
        # The completion receipt is the evidence that permits the program to
        # clear this state.  Allow no other reader issue through this narrow
        # bridge, and keep the underlying audit's pending disclosure intact
        # until research_program records the receipt.
        summary["status"] = "ready"
        summary["pending_method_revalidation"] = {
            "accepted_for_completion_receipt": True,
            "issue": expected_pending_issue,
            "revision_id": pending_revalidation_revision.get("revision_id"),
        }
    if summary.get("status") != "ready":
        raise CompilerWorkerV2Error(
            "outcome reader-quality audit failed before completion receipt: "
            + reader_quality_failure_reason(summary)
        )
    return summary


def reader_quality_summary(audit: dict[str, Any], source_id: str, root: Path) -> dict[str, Any]:
    pages = audit.get("pages") if isinstance(audit.get("pages"), list) else []
    current_page = {}
    review_pages: list[dict[str, Any]] = []
    for page in pages:
        if not isinstance(page, dict):
            continue
        page_summary = reader_quality_page_summary(page, root)
        if str(page.get("path") or "").endswith(f"{source_id}_outcome.html"):
            current_page = page_summary
        if page_summary.get("status") != "ready":
            review_pages.append(page_summary)
    index = audit.get("index") if isinstance(audit.get("index"), dict) else {}
    portal = audit.get("portal") if isinstance(audit.get("portal"), dict) else {}
    status = str(audit.get("status") or "needs_review")
    if not current_page:
        status = "needs_review"
    return {
        "status": status,
        "source_id": source_id,
        "scope": audit.get("scope", "whole_vault"),
        "page_count": int(audit.get("page_count") or 0),
        "ready_count": int(audit.get("ready_count") or 0),
        "needs_review_count": int(audit.get("needs_review_count") or 0),
        "current_page": current_page,
        "review_pages": review_pages,
        "index": {
            "status": index.get("status"),
            "issues": index.get("issues") or [],
        },
        "portal": {
            "status": portal.get("status"),
            "issues": portal.get("issues") or [],
        },
        "blocked_reasons": audit.get("blocked_reasons") or [],
    }


def reader_quality_page_summary(page: dict[str, Any], root: Path) -> dict[str, Any]:
    path = Path(str(page.get("path") or ""))
    display_path = str(path)
    if path.is_absolute():
        try:
            display_path = path.resolve().relative_to(root.resolve()).as_posix()
        except ValueError:
            display_path = str(path)
    issue_fields = (
        "missing_required_texts",
        "asset_issues",
        "source_url_issues",
        "navigation_issues",
        "layout_issues",
        "duplicate_content_issues",
        "generic_text_issues",
        "style_issues",
        "placeholder_issues",
        "internal_language_risks",
    )
    return {
        "source_id": path.name.replace("_outcome.html", "") if path.name else "",
        "status": page.get("status"),
        "path": display_path,
        "title": page.get("title"),
        "issue_count": int(page.get("issue_count") or 0),
        "issues": {
            field: page.get(field) or []
            for field in issue_fields
            if page.get(field)
        },
    }


def reader_quality_failure_reason(summary: dict[str, Any]) -> str:
    current = summary.get("current_page") if isinstance(summary.get("current_page"), dict) else {}
    if current and current.get("status") != "ready":
        return json.dumps({
            "current_page": current,
        }, ensure_ascii=False)
    return json.dumps({
        "status": summary.get("status"),
        "needs_review_count": summary.get("needs_review_count"),
        "review_pages": summary.get("review_pages"),
        "index": summary.get("index"),
        "portal": summary.get("portal"),
        "blocked_reasons": summary.get("blocked_reasons"),
    }, ensure_ascii=False)


def outcome_recovery_checkpoint(root: Path, vault: Path, source_id: str) -> dict[str, Any]:
    paths = {
        "current_page": vault / "outcome-pages" / f"{source_id}_outcome.html",
        "html_index": vault / "outcome-pages" / "index.html",
        "obsidian_portal": vault / "成果頁總覽.md",
    }
    return {
        "source_id": source_id,
        "paths": {
            key: relative_to_root(path, root, f"outcome {key}").relative_to(root).as_posix()
            for key, path in paths.items()
        },
        "preexisting_sha256": {
            key: sha256_bytes(path.read_bytes()) if path.is_file() else ""
            for key, path in paths.items()
        },
    }


def recover_incremental_outcome(
    root: Path,
    checkpoint: dict[str, Any],
) -> dict[str, Any] | None:
    paths = checkpoint.get("paths") if isinstance(checkpoint.get("paths"), dict) else {}
    previous = (
        checkpoint.get("preexisting_sha256")
        if isinstance(checkpoint.get("preexisting_sha256"), dict)
        else {}
    )
    resolved: dict[str, Path] = {}
    for key in ("current_page", "html_index", "obsidian_portal"):
        raw = str(paths.get(key) or "")
        if not raw:
            return None
        path = relative_to_root(root / raw, root, f"outcome {key}")
        if not path.is_file():
            return None
        before_hash = str(previous.get(key) or "")
        if before_hash and sha256_bytes(path.read_bytes()) == before_hash:
            return None
        resolved[key] = path
    source_id = str(checkpoint.get("source_id") or "")
    if source_id not in resolved["current_page"].read_text(encoding="utf-8", errors="replace"):
        return None
    return {
        "status": "ready",
        "output_dir": str(resolved["current_page"].parent),
        "html_index": str(resolved["html_index"]),
        "obsidian_portal": str(resolved["obsidian_portal"]),
        "current_page": str(resolved["current_page"]),
        "index_pages": [str(resolved["html_index"])],
        "pages": [str(resolved["current_page"])],
        "recovered_without_write": True,
    }


def artifact_manifest(paths: list[object], root: Path) -> list[dict[str, str]]:
    manifest: list[dict[str, str]] = []
    seen: set[Path] = set()
    for raw in paths:
        if not raw:
            continue
        path = Path(str(raw))
        resolved = path.resolve() if path.is_absolute() else (root / path).resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        if not resolved.is_file():
            raise CompilerWorkerV2Error(f"completion artifact is missing: {resolved}")
        try:
            relative = resolved.relative_to(root.resolve())
        except ValueError as exc:
            raise CompilerWorkerV2Error("completion artifact is outside the project root") from exc
        manifest.append({
            "path": relative.as_posix(),
            "sha256": sha256_bytes(resolved.read_bytes()),
        })
    return manifest


def portable_tool_result(
    payload: dict[str, Any],
    *,
    root: Path,
    scalar_path_fields: tuple[str, ...],
    list_path_fields: tuple[str, ...],
) -> dict[str, Any]:
    portable = json.loads(json.dumps(payload, ensure_ascii=False))
    for field in scalar_path_fields:
        value = portable.get(field)
        if value:
            resolved = Path(str(value))
            resolved = resolved.resolve() if resolved.is_absolute() else (root / resolved).resolve()
            try:
                portable[field] = resolved.relative_to(root.resolve()).as_posix()
            except ValueError as exc:
                raise CompilerWorkerV2Error(f"{field} is outside the project root") from exc
    for field in list_path_fields:
        values = portable.get(field) or []
        if not isinstance(values, list):
            raise CompilerWorkerV2Error(f"{field} must be a path list")
        converted: list[str] = []
        for value in values:
            resolved = Path(str(value))
            resolved = resolved.resolve() if resolved.is_absolute() else (root / resolved).resolve()
            try:
                converted.append(resolved.relative_to(root.resolve()).as_posix())
            except ValueError as exc:
                raise CompilerWorkerV2Error(f"{field} contains a path outside the project root") from exc
        portable[field] = converted
    return portable


def complete_v2_job(root: Path, job_id: str, receipt_path: Path) -> None:
    path = job_file(root, job_id)
    if not path.exists():
        raise CompilerWorkerV2Error("v2 job is missing at completion")
    job = read_json(path)
    job.update({
        "status": "completed",
        "updated_at": iso_now(),
        "completed_at": iso_now(),
        "completion_receipt_path": receipt_path.relative_to(root).as_posix(),
    })
    write_v2_job(root, job)


def _relative_existing_file(root: Path, raw: Path, label: str) -> Path:
    path = relative_to_root(raw if raw.is_absolute() else root / raw, root, label)
    if not path.is_file():
        raise CompilerWorkerV2Error(f"{label} is missing: {path}")
    return path


def _frontmatter_scalar(path: Path, key: str) -> str:
    text = path.read_text(encoding="utf-8", errors="replace")
    match = re.search(rf"(?m)^{re.escape(key)}:\s*\"?([^\"\n]+)\"?\s*$", text)
    return match.group(1).strip() if match else ""


def command_rebind_existing_completion(args: argparse.Namespace) -> int:
    """Retired unsafe repair: existing files are not evidence of completion."""
    raise CompilerWorkerV2Error(
        "rebind-existing-completion is retired: resume the original project command "
        "or use a source-bound revision; existing files alone cannot establish completion"
    )


def command_replace_completion_assets(args: argparse.Namespace) -> int:
    root = args.root.resolve()
    vault = relative_to_root(args.vault if args.vault.is_absolute() else root / args.vault, root, "vault")
    if not vault.is_dir():
        raise CompilerWorkerV2Error("selected vault is missing")
    job = read_json(job_file(root, args.job_id))
    if str(job.get("source_id") or "") != args.source_id:
        raise CompilerWorkerV2Error("job source_id does not match requested source_id")
    if job.get("status") != "completed" or not job.get("completion_receipt_path"):
        raise CompilerWorkerV2Error("fresh asset replacement requires an existing completed job")
    package = relative_to_root(root / str(job.get("package_dir") or ""), root, "package")
    if not package.is_dir():
        raise CompilerWorkerV2Error("job package is missing")
    review_packet_path = package / "review_packet.json"
    canonical_source_path = package / "canonical_source.md"
    if not review_packet_path.is_file() or not canonical_source_path.is_file():
        raise CompilerWorkerV2Error("fresh source capture package is incomplete")
    review_packet = read_json(review_packet_path)
    for field in ("job_id", "source_id", "source_input_kind", "source_identity"):
        if str(job.get(field) or "") != str(review_packet.get(field) or ""):
            raise CompilerWorkerV2Error(f"job and review packet disagree on {field}")
    if str(review_packet.get("source_review_status") or "") != "passed":
        raise CompilerWorkerV2Error("source review packet has not passed")

    previous_raw = args.previous_completion_receipt or Path(str(job.get("completion_receipt_path") or ""))
    previous_path = _relative_existing_file(root, previous_raw, "previous completion receipt")
    previous = read_json(previous_path)
    if (
        previous.get("schema_version") != COMPLETION_SCHEMA_VERSION
        or previous.get("status") != "completed"
        or previous.get("payload_sha256") != canonical_payload_sha256(previous)
    ):
        raise CompilerWorkerV2Error("previous completion receipt is invalid")
    for field in ("job_id", "source_id", "source_input_kind", "source_identity"):
        if str(previous.get(field) or "") != str(job.get(field) or ""):
            raise CompilerWorkerV2Error(f"previous completion receipt disagrees on {field}")

    source_note = _relative_existing_file(root, args.source_note, "source note")
    application_note = _relative_existing_file(root, args.application_note, "application note")
    knowledge_points = [
        _relative_existing_file(root, item, "knowledge point")
        for item in args.knowledge_point
    ]
    if not knowledge_points:
        raise CompilerWorkerV2Error("at least one knowledge point is required")
    assets = [
        _relative_existing_file(root, item, "fresh infographic asset")
        for item in args.infographic_asset
    ]
    if not assets:
        raise CompilerWorkerV2Error("at least one fresh infographic asset is required")
    outcome_page = _relative_existing_file(root, args.outcome_page, "outcome page")
    html_index = _relative_existing_file(root, args.html_index, "outcome html index") if args.html_index else None
    obsidian_portal = (
        _relative_existing_file(root, args.obsidian_portal, "outcome portal")
        if args.obsidian_portal
        else None
    )
    for label, path in {
        "source note": source_note,
        "application note": application_note,
        "outcome page": outcome_page,
        **{f"knowledge point {index + 1}": path for index, path in enumerate(knowledge_points)},
        **{f"fresh infographic asset {index + 1}": path for index, path in enumerate(assets)},
    }.items():
        try:
            path.relative_to(vault.resolve())
        except ValueError as exc:
            raise CompilerWorkerV2Error(f"{label} is outside the selected vault") from exc

    previous_adapter = previous.get("adapter") if isinstance(previous.get("adapter"), dict) else {}
    previous_assets = [
        _relative_existing_file(root, Path(str(item)), "previous infographic asset")
        for item in previous_adapter.get("assets") or []
    ]
    if not previous_assets:
        raise CompilerWorkerV2Error("previous completion receipt has no infographic assets")
    previous_asset_paths = {path.resolve() for path in previous_assets}
    fresh_asset_paths = {path.resolve() for path in assets}
    if previous_asset_paths == fresh_asset_paths or previous_asset_paths.intersection(fresh_asset_paths):
        raise CompilerWorkerV2Error("fresh asset replacement must not reuse previous infographic paths")
    previous_asset_hashes = {sha256_bytes(path.read_bytes()) for path in previous_assets}
    fresh_asset_hashes = {sha256_bytes(path.read_bytes()) for path in assets}
    if previous_asset_hashes.intersection(fresh_asset_hashes):
        raise CompilerWorkerV2Error("fresh asset replacement must not reuse previous infographic bytes")

    source_slice = package / f"{args.source_id}_verified_{validated_identifier(args.receipt_suffix, 'receipt_suffix')}.md"
    source_slice_text = "\n".join([
        f"# {review_packet.get('title') or args.source_id} - fresh infographic replacement",
        "",
        f"- Job: `{args.job_id}`",
        f"- Source ID: `{args.source_id}`",
        f"- Source identity: `{review_packet.get('source_identity')}`",
        f"- Canonical body SHA-256: `{review_packet.get('canonical_body_sha256')}`",
        f"- Fresh capture: `{canonical_source_path.relative_to(root).as_posix()}`",
        f"- Previous completion receipt: `{previous_path.relative_to(root).as_posix()}`",
        "",
        "## Bound Vault Artifacts",
        f"- Source note: `{source_note.relative_to(root).as_posix()}`",
        f"- Application note: `{application_note.relative_to(root).as_posix()}`",
        *[
            f"- Knowledge point: `{path.relative_to(root).as_posix()}`"
            for path in knowledge_points
        ],
        *[
            f"- Fresh infographic asset: `{path.relative_to(root).as_posix()}`"
            for path in assets
        ],
        f"- Outcome page: `{outcome_page.relative_to(root).as_posix()}`",
        "",
        "This receipt replaces only the active infographic assets for the same verified source completion. "
        "Earlier infographic files remain audit history; active product state must read this fresh-image receipt.",
        "",
    ])
    source_slice.write_text(source_slice_text, encoding="utf-8")

    previous_kp_path = _relative_existing_file(
        root,
        Path(str(previous.get("knowledge_package_path") or "")),
        "previous Knowledge Package",
    )
    previous_kp = read_json(previous_kp_path)
    source = previous_kp.get("source") if isinstance(previous_kp.get("source"), dict) else {}
    if (
        str(previous_kp.get("job_id") or "") != str(job.get("job_id") or "")
        or source.get("source_id") != args.source_id
        or source.get("source_identity") != job.get("source_identity")
    ):
        raise CompilerWorkerV2Error("previous Knowledge Package identity mismatch")
    suffix = validated_identifier(args.receipt_suffix, "receipt_suffix")
    kp_path = package / f"knowledge_package_{suffix}.json"
    knowledge_package = json.loads(json.dumps(previous_kp, ensure_ascii=False))
    analysis = knowledge_package.get("analysis") if isinstance(knowledge_package.get("analysis"), dict) else {}
    analysis.update({"mode": "verified_fresh_infographic_replacement"})
    knowledge_package["analysis"] = analysis
    projection = knowledge_package.get("projection") if isinstance(knowledge_package.get("projection"), dict) else {}
    projection.update({"target": "obsidian_vault", "mode": "verified_fresh_infographic_replacement"})
    knowledge_package["projection"] = projection
    knowledge_package["fresh_image_replacement"] = {
        "previous_completion_receipt": previous_path.relative_to(root).as_posix(),
        "previous_assets": [
            {"path": path.relative_to(root).as_posix(), "sha256": sha256_bytes(path.read_bytes())}
            for path in previous_assets
        ],
        "fresh_assets": [
            {"path": path.relative_to(root).as_posix(), "sha256": sha256_bytes(path.read_bytes())}
            for path in assets
        ],
        "source_note": source_note.relative_to(root).as_posix(),
        "application_note": application_note.relative_to(root).as_posix(),
        "knowledge_points": [path.relative_to(root).as_posix() for path in knowledge_points],
        "outcome_page": outcome_page.relative_to(root).as_posix(),
        "updated_at": iso_now(),
    }
    write_json(kp_path, knowledge_package)

    portable_adapter = {
        "status": "ready",
        "mode": "verified_fresh_infographic_replacement",
        "source_identity": job.get("source_identity"),
        "source_note": source_note.relative_to(root).as_posix(),
        "application_note": application_note.relative_to(root).as_posix(),
        "knowledge_points": [path.relative_to(root).as_posix() for path in knowledge_points],
        "asset": assets[0].relative_to(root).as_posix(),
        "assets": [path.relative_to(root).as_posix() for path in assets],
        "verification": {
            "passed": True,
            "mode": "verified_fresh_infographic_replacement",
            "fresh_capture_sha256": str(review_packet.get("canonical_body_sha256") or ""),
            "previous_completion_receipt": previous_path.relative_to(root).as_posix(),
            "previous_asset_sha256": sorted(previous_asset_hashes),
            "fresh_asset_sha256": sorted(fresh_asset_hashes),
        },
    }
    portable_outcome = {
        "status": "ready",
        "mode": "verified_fresh_infographic_replacement",
        "current_page": outcome_page.relative_to(root).as_posix(),
        "html_index": html_index.relative_to(root).as_posix() if html_index else "",
        "obsidian_portal": obsidian_portal.relative_to(root).as_posix() if obsidian_portal else "",
    }
    manifest_paths: list[object] = [
        source_note,
        application_note,
        *knowledge_points,
        *assets,
        outcome_page,
    ]
    manifest = artifact_manifest(manifest_paths, root)
    completion_path = package / f"completion_receipt_{suffix}.json"
    completion = {
        "schema_version": COMPLETION_SCHEMA_VERSION,
        "status": "completed",
        "job_id": args.job_id,
        "source_id": args.source_id,
        "source_input_kind": job.get("source_input_kind"),
        "source_identity": job.get("source_identity"),
        "first_accepted_at": job.get("first_accepted_at"),
        "completed_at": iso_now(),
        "knowledge_package_path": kp_path.relative_to(root).as_posix(),
        "knowledge_package_sha256": sha256_bytes(kp_path.read_bytes()),
        "source_slice_path": source_slice.relative_to(root).as_posix(),
        "source_slice_sha256": sha256_bytes(source_slice.read_bytes()),
        "adapter": portable_adapter,
        "outcome": portable_outcome,
        "reader_quality_audit": {
            "status": "ready",
            "mode": "verified_fresh_infographic_replacement",
            "current_page": outcome_page.relative_to(root).as_posix(),
        },
        "coverage_audit": {
            "schema_version": COVERAGE_AUDIT_SCHEMA_VERSION,
            "status": "complete",
            "mode": "verified_fresh_infographic_replacement",
            "fresh_capture_sha256": str(review_packet.get("canonical_body_sha256") or ""),
        },
        "artifact_manifest": manifest,
        "fresh_image_replacement": {
            "active_record_replaced_by": "verified_replacement",
            "old_completion_is_audit_only": True,
            "previous_completion_receipt": previous_path.relative_to(root).as_posix(),
            "fresh_capture_package": package.relative_to(root).as_posix(),
            "previous_assets": portable_adapter["verification"]["previous_asset_sha256"],
            "fresh_assets": portable_adapter["verification"]["fresh_asset_sha256"],
        },
    }
    completion["payload_sha256"] = canonical_payload_sha256(completion)
    write_json(completion_path, completion)
    if read_json(completion_path).get("payload_sha256") != canonical_payload_sha256(read_json(completion_path)):
        raise CompilerWorkerV2Error("completion receipt readback hash mismatch")
    complete_v2_job(root, args.job_id, completion_path)
    write_json(package / "receipt.json", {
        "schema_version": SCHEMA_VERSION,
        "status": "completed",
        "stage": "completed",
        "checkpoint": "verified_fresh_infographic_replacement",
        "job_id": args.job_id,
        "source_id": args.source_id,
        "source_identity": job.get("source_identity"),
        "completion_receipt": completion_path.relative_to(root).as_posix(),
        "updated_at": iso_now(),
    })
    result = {
        "status": "completed",
        "stage": "verified_fresh_infographic_replacement",
        "job_id": args.job_id,
        "source_id": args.source_id,
        "previous_completion_receipt": previous_path.relative_to(root).as_posix(),
        "completion_receipt": completion_path.relative_to(root).as_posix(),
        "artifact_count": len(manifest),
        "assets": [path.relative_to(root).as_posix() for path in assets],
    }
    print(json.dumps(result, ensure_ascii=False))
    return 0


def bound_completion_path(root: Path, package: Path, kp_path: Path, kp: dict[str, Any]) -> Path:
    """Use the accepted job's explicit receipt, including named legacy revisions."""
    default = package / 'completion_receipt.json'
    job_path = job_file(root, str(kp.get('job_id') or ''))
    if not job_path.is_file(): return default
    job = read_json(job_path)
    value = str(job.get('completion_receipt_path') or '')
    if not value: return default
    target = relative_to_root(root / value, root, 'bound completion receipt')
    if target.parent != package:
        return default  # Historical packages retain their own receipt binding.
    if target != default:
        receipt = read_json(target)
        if (receipt.get('payload_sha256') != canonical_payload_sha256(receipt)
                or receipt.get('schema_version') != COMPLETION_SCHEMA_VERSION
                or receipt.get('status') != 'completed'
                or receipt.get('source_id') != (kp.get('source') or {}).get('source_id')
                or receipt.get('knowledge_package_path') != kp_path.relative_to(root).as_posix()
                or receipt.get('job_id') != kp.get('job_id')
                or receipt.get('source_identity') != (kp.get('source') or {}).get('source_identity')):
            raise CompilerWorkerV2Error('named completion receipt binding mismatch')
    return target


def validate_job_package_binding(
    root: Path,
    kp_path: Path,
    kp: dict[str, Any],
) -> dict[str, Any]:
    job_id = str(kp.get("job_id") or "")
    path = job_file(root, job_id)
    if not path.is_file():
        raise CompilerWorkerV2Error("Knowledge Package job_id does not exist")
    job = read_json(path)
    source = kp.get("source") if isinstance(kp.get("source"), dict) else {}
    amendment_path = kp_path.parent / "application_amendment.json"
    if amendment_path.is_file():
        amendment = read_json(amendment_path)
        if amendment.get("payload_sha256") != canonical_payload_sha256(amendment) or amendment.get("state") != "completed":
            raise CompilerWorkerV2Error("application amendment pending; resume the same amend-application request before continuing")
    if (kp.get("revision") or {}).get("mode") == "same_job_text_revision":
        text_revision.verify_origin(sys.modules[__name__], root, kp_path.parent)
    job = text_revision.historical_job(sys.modules[__name__], root, job, kp_path)
    expected_package = kp_path.parent.relative_to(root).as_posix()
    checks = {
        "source_id": str(source.get("source_id") or ""),
        "source_input_kind": str(source.get("source_input_kind") or ""),
        "source_identity": str(source.get("source_identity") or ""),
        "package_dir": expected_package,
    }
    for field, expected in checks.items():
        if str(job.get(field) or "") != expected:
            raise CompilerWorkerV2Error(f"Knowledge Package does not match accepted job {field}")
    if job.get("status") not in {"running", "awaiting_imagegen", "committing", "completed"}:
        raise CompilerWorkerV2Error(f"v2 job is not ready for projection: {job.get('status')}")
    completion_path = str(job.get("completion_receipt_path") or "")
    expected_completion = bound_completion_path(root, kp_path.parent, kp_path, kp).relative_to(root).as_posix()
    if completion_path and completion_path != expected_completion:
        raise CompilerWorkerV2Error("v2 job is bound to another completion receipt")
    return job


def infographic_origin_package(
    root: Path,
    package: Path,
    kp: dict[str, Any],
) -> Path:
    revision = kp.get("revision") if isinstance(kp.get("revision"), dict) else {}
    origin_value = str(revision.get("origin_package") or "").strip()
    if not origin_value:
        return package
    return relative_to_root(
        root / origin_value,
        root,
        "infographic revision origin package",
    )


def read_current_infographic_pointer(
    root: Path,
    package: Path,
    kp: dict[str, Any],
) -> dict[str, Any] | None:
    origin_package = infographic_origin_package(root, package, kp)
    pointer_path = origin_package / "infographic_current.json"
    if not pointer_path.is_file():
        return None
    pointer = read_json(pointer_path)
    source = kp.get("source") if isinstance(kp.get("source"), dict) else {}
    if (
        pointer.get("schema_version") != INFOGRAPHIC_CURRENT_POINTER_VERSION
        or pointer.get("payload_sha256") != canonical_payload_sha256(pointer)
        or pointer.get("source_id") != source.get("source_id")
        or pointer.get("source_identity") != source.get("source_identity")
    ):
        raise CompilerWorkerV2Error("current infographic pointer is invalid")
    completion_path = relative_to_root(
        root / str(pointer.get("current_completion_path") or ""),
        root,
        "current infographic completion",
    )
    if (
        not completion_path.is_file()
        or sha256_bytes(completion_path.read_bytes())
        != pointer.get("current_completion_file_sha256")
    ):
        raise CompilerWorkerV2Error(
            "current infographic pointer completion readback mismatch"
        )
    current_completion = read_json(completion_path)
    if (
        current_completion.get("schema_version") != COMPLETION_SCHEMA_VERSION
        or current_completion.get("status") != "completed"
        or current_completion.get("source_id") != (kp.get("source") or {}).get("source_id")
        or current_completion.get("source_identity") != (kp.get("source") or {}).get("source_identity")
        or current_completion.get("payload_sha256")
        != pointer.get("current_completion_payload_sha256")
        or current_completion.get("payload_sha256")
        != canonical_payload_sha256(current_completion)
    ):
        raise CompilerWorkerV2Error(
            "current infographic pointer completion receipt mismatch"
        )
    for item in pointer.get("selected_infographic_assets") or []:
        if not isinstance(item, dict):
            raise CompilerWorkerV2Error("current infographic asset pointer is invalid")
        asset = relative_to_root(
            root / str(item.get("path") or ""),
            root,
            "current infographic asset",
        )
        if (
            not asset.is_file()
            or sha256_bytes(asset.read_bytes()) != item.get("sha256")
        ):
            raise CompilerWorkerV2Error(
                "current infographic asset readback mismatch"
            )
    return pointer


def write_current_infographic_pointer(
    root: Path,
    package: Path,
    kp: dict[str, Any],
    completion_path: Path,
    completion: dict[str, Any],
) -> dict[str, Any] | None:
    revision = kp.get("revision") if isinstance(kp.get("revision"), dict) else {}
    if revision.get("kind") != "infographic_revision":
        return None
    origin_package = infographic_origin_package(root, package, kp)
    superseded_path = relative_to_root(
        root / str(revision.get("supersedes_completion_path") or ""),
        root,
        "superseded completion receipt",
    )
    if not superseded_path.is_file():
        raise CompilerWorkerV2Error("superseded completion receipt is missing")
    superseded_completion = read_json(superseded_path)
    if (
        superseded_completion.get("payload_sha256")
        != revision.get("supersedes_completion_payload_sha256")
        or superseded_completion.get("payload_sha256")
        != canonical_payload_sha256(superseded_completion)
    ):
        raise CompilerWorkerV2Error("superseded completion receipt hash mismatch")
    adapter = completion.get("adapter") if isinstance(completion.get("adapter"), dict) else {}
    selected_assets = artifact_manifest(list(adapter.get("assets") or []), root)
    if not selected_assets:
        raise CompilerWorkerV2Error(
            "infographic revision completion has no selected assets"
        )
    pointer = {
        "schema_version": INFOGRAPHIC_CURRENT_POINTER_VERSION,
        "status": "current",
        "source_id": completion.get("source_id"),
        "source_identity": completion.get("source_identity"),
        "current_revision_id": revision.get("revision_id"),
        "current_completion_path": completion_path.relative_to(root).as_posix(),
        "current_completion_file_sha256": sha256_bytes(completion_path.read_bytes()),
        "current_completion_payload_sha256": completion.get("payload_sha256"),
        "supersedes_completion_path": superseded_path.relative_to(root).as_posix(),
        "supersedes_completion_payload_sha256": superseded_completion.get("payload_sha256"),
        "selected_infographic_assets": selected_assets,
        "superseded_artifact_paths": [
            str(item.get("path") or "")
            for item in superseded_completion.get("artifact_manifest") or []
            if isinstance(item, dict) and str(item.get("path") or "")
        ],
        "updated_at": iso_now(),
    }
    pointer_path = origin_package / "infographic_current.json"
    if pointer_path.exists():
        previous = read_json(pointer_path)
        if previous.get("payload_sha256") != canonical_payload_sha256(previous):
            raise CompilerWorkerV2Error("previous infographic pointer hash mismatch")
        pointer["superseded_artifact_paths"] = sorted(set(
            pointer["superseded_artifact_paths"] + previous.get("superseded_artifact_paths", [])))
    pointer["payload_sha256"] = canonical_payload_sha256(pointer)
    write_json(pointer_path, pointer)
    readback = read_current_infographic_pointer(root, package, kp)
    if readback != pointer:
        raise CompilerWorkerV2Error("current infographic pointer readback mismatch")
    return pointer


def outcome_revision_hash(root: Path, package: Path, completion: dict[str, Any], path: str) -> str | None:
    """Validate a derived HTML revision without rebinding source or image evidence."""
    revision_path = package / "outcome_projection_revisions.json"
    if not revision_path.exists():
        return None
    record = read_json(revision_path)
    if (record.get("schema_version") != "outcome_projection_revisions_v1"
            or record.get("payload_sha256") != canonical_payload_sha256(record)
            or record.get("completion_payload_sha256") != completion.get("payload_sha256")):
        raise CompilerWorkerV2Error("invalid outcome projection revision binding")
    expected_path = f"{vault_relative_posix()}/outcome-pages/{completion['source_id']}_outcome.html"
    if record.get("artifact_path") != expected_path or path != expected_path:
        return None
    previous = next((item["sha256"] for item in completion["artifact_manifest"] if item["path"] == path), None)
    revisions = record.get("revisions")
    if not previous or not isinstance(revisions, list) or not revisions:
        raise CompilerWorkerV2Error("empty outcome projection revision chain")
    for revision in revisions:
        if revision.get("previous_sha256") != previous or not re.fullmatch(r"[0-9a-f]{64}", str(revision.get("sha256") or "")):
            raise CompilerWorkerV2Error("broken outcome projection revision chain")
        previous = revision["sha256"]
    return previous


def refresh_completed_outcome(root: Path, kp_path: Path) -> dict[str, Any]:
    """Regenerate only the derived reader page; preserve the original receipt."""
    kp = read_json(kp_path)
    package = kp_path.parent
    completion = completed_receipt_readback(
        root, package, kp_path, kp, allow_derived_outcome_drift=True,
    )
    if not completion:
        raise CompilerWorkerV2Error("outcome refresh requires a verified completion")
    original = read_json(bound_completion_path(root, package, kp_path, kp))
    source_id = original["source_id"]
    portable = f"{vault_relative_posix()}/outcome-pages/{source_id}_outcome.html"
    target = relative_to_root(root / portable, root, "derived outcome page")
    prior = target.read_bytes()
    expected_hash = outcome_revision_hash(root, package, original, portable) or next(
        (item.get("sha256") for item in original.get("artifact_manifest") or []
         if isinstance(item, dict) and item.get("path") == portable),
        None,
    )
    if not isinstance(expected_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_hash):
        raise CompilerWorkerV2Error("completed outcome has no valid expected hash")
    observed_hash = sha256_bytes(prior)
    unregistered_backup: Path | None = None
    if observed_hash != expected_hash:
        backup_dir = package / "outcome_projection_unregistered"
        backup_dir.mkdir(parents=True, exist_ok=True)
        unregistered_backup = backup_dir / f"{source_id}_{observed_hash}.html"
        if unregistered_backup.exists() and unregistered_backup.read_bytes() != prior:
            raise CompilerWorkerV2Error("unregistered outcome backup hash collision")
        if not unregistered_backup.exists():
            unregistered_backup.write_bytes(prior)
    candidate = target.with_name(f".{source_id}_outcome_revision_candidate.html")
    if candidate.exists():
        raise CompilerWorkerV2Error("outcome revision candidate already exists; inspect interrupted work")
    try:
        from tools.outcome_page_generator import generate_outcome_page
    except ModuleNotFoundError:
        from outcome_page_generator import generate_outcome_page
    result = generate_outcome_page(source_id=source_id, vault=vault_path(root), output=candidate)
    # Recheck every source/input after rendering, before changing the live page.
    completed_receipt_readback(root, package, kp_path, kp, allow_derived_outcome_drift=True)
    if target.read_bytes() != prior:
        raise CompilerWorkerV2Error("outcome changed during refresh; candidate retained for inspection")
    new_bytes = candidate.read_bytes()
    revision_path = package / "outcome_projection_revisions.json"
    record = read_json(revision_path) if revision_path.exists() else {
        "schema_version": "outcome_projection_revisions_v1",
        "completion_payload_sha256": original["payload_sha256"],
        "artifact_path": portable, "revisions": [],
    }
    if unregistered_backup is not None:
        observations = record.setdefault("unregistered_live_observations", [])
        if not isinstance(observations, list):
            raise CompilerWorkerV2Error("invalid unregistered outcome observation list")
        if not any(isinstance(item, dict) and item.get("observed_sha256") == observed_hash for item in observations):
            observations.append({
                "observed_sha256": observed_hash,
                "expected_chain_sha256": expected_hash,
                "backup_path": unregistered_backup.relative_to(root).as_posix(),
                "observed_at": iso_now(),
                "status": "unregistered_live_projection",
            })
    record["revisions"].append({"previous_sha256": expected_hash, "sha256": sha256_bytes(new_bytes),
        "generated_at": iso_now(), "generator_sha256": sha256_bytes((root / "tools/outcome_page_generator.py").read_bytes()),
        "reason": "rebuild derived reader page from unchanged verified vault content"})
    record["payload_sha256"] = canonical_payload_sha256(record)
    # A pending record plus candidate is recoverable after interruption; no old
    # completion hash is rewritten to manufacture ingestion acceptance.
    write_json(revision_path, record)
    os.replace(candidate, target)
    completed_receipt_readback(root, package, kp_path, kp)
    return {"status": "ready", "page": portable, "revision_receipt": revision_path.relative_to(root).as_posix(),
            "source_and_images_unchanged": True, "original_completion_preserved": True,
            "unregistered_live_backup": unregistered_backup.relative_to(root).as_posix() if unregistered_backup else "",
            "observed_live_sha256": observed_hash if unregistered_backup else ""}


def receipt_artifact_matches(path: Path, expected_hash: str) -> bool:
    """Generated Markdown/JSON may change only LF/CRLF; other bytes stay strict.

    This reconstructs bytes matching the original receipt, rather than trusting
    a new normalized hash. Raw input identity checks remain separate and exact.
    """
    raw = path.read_bytes()
    if sha256_bytes(raw) == expected_hash:
        return True
    if (path.name == "source_capture.md" or path.name.startswith("source_capture_api.")
            or path.name.startswith("source_capture_youtube.")
            or path.suffix.lower() not in {".md", ".json"} or b"\x00" in raw):
        return False
    try:
        raw.decode("utf-8")
    except UnicodeDecodeError:
        return False
    lf = raw.replace(b"\r\n", b"\n")
    if b"\r" in lf:
        return False
    return expected_hash in {sha256_bytes(lf), sha256_bytes(lf.replace(b"\n", b"\r\n"))}


def completed_receipt_readback(
    root: Path,
    package: Path,
    kp_path: Path,
    kp: dict[str, Any],
    *,
    allow_derived_outcome_drift: bool = False,
) -> dict[str, Any] | None:
    completion_path = bound_completion_path(root, package, kp_path, kp)
    if not completion_path.exists():
        return None
    completion = read_json(completion_path)
    if completion.get("schema_version") != COMPLETION_SCHEMA_VERSION or completion.get("status") != "completed":
        raise CompilerWorkerV2Error("existing completion receipt is not a completed v2 receipt")
    if completion.get("payload_sha256") != canonical_payload_sha256(completion):
        raise CompilerWorkerV2Error("existing completion receipt hash mismatch")
    try:
        try:
            from .completion_revisions import read_current
        except ImportError:
            from completion_revisions import read_current
        accepted_changes = read_current(root, package, completion)
    except (ValueError, KeyError, OSError) as exc:
        raise CompilerWorkerV2Error(str(exc)) from exc
    if completion.get("job_id") != kp.get("job_id"):
        raise CompilerWorkerV2Error("existing completion receipt job mismatch")
    source = kp.get("source") or {}
    if completion.get("source_identity") != source.get("source_identity"):
        raise CompilerWorkerV2Error("existing completion receipt source identity mismatch")
    kp_revision = accepted_changes.get(completion.get("knowledge_package_path"), {})
    if not receipt_artifact_matches(kp_path, kp_revision.get("sha256", completion.get("knowledge_package_sha256"))):
        raise CompilerWorkerV2Error("completed Knowledge Package bytes changed")
    source_slice = relative_to_root(
        root / str(completion.get("source_slice_path") or ""),
        root,
        "completed source slice",
    )
    slice_revision = accepted_changes.get(completion.get("source_slice_path"), {})
    if not source_slice.is_file() or not receipt_artifact_matches(source_slice, slice_revision.get("sha256", completion.get("source_slice_sha256"))):
        raise CompilerWorkerV2Error("completed source slice readback mismatch")
    ledger_value = str(completion.get("image_attempt_ledger_path") or "")
    ledger_hash = str(completion.get("image_attempt_ledger_sha256") or "")
    if bool(ledger_value) != bool(ledger_hash):
        raise CompilerWorkerV2Error("completion image attempt ledger fields are incomplete")
    if ledger_value:
        ledger_path = relative_to_root(
            root / ledger_value,
            root,
            "completed image attempt ledger",
        )
        if not ledger_path.is_file() or not receipt_artifact_matches(ledger_path, ledger_hash):
            raise CompilerWorkerV2Error("completed image attempt ledger readback mismatch")
    current_pointer = read_current_infographic_pointer(root, package, kp)
    current_completion_path = (
        str(current_pointer.get("current_completion_path") or "")
        if current_pointer
        else ""
    )
    this_completion_path = completion_path.relative_to(root).as_posix()
    superseded_paths = (
        set(str(item) for item in current_pointer.get("superseded_artifact_paths") or [])
        if current_pointer and current_completion_path != this_completion_path
        else set()
    )
    derived_outcome_path = f"{vault_relative_posix()}/outcome-pages/{completion['source_id']}_outcome.html"
    for item in completion.get("artifact_manifest") or []:
        if not isinstance(item, dict):
            raise CompilerWorkerV2Error("existing completion artifact manifest is invalid")
        portable_artifact_path = str(item.get("path") or "")
        accepted_change = accepted_changes.get(portable_artifact_path, {})
        artifact_is_superseded = portable_artifact_path in superseded_paths
        artifact_path = Path(accepted_change.get("current_path", portable_artifact_path))
        artifact = relative_to_root(
            artifact_path if artifact_path.is_absolute() else root / artifact_path,
            root,
            "completed artifact",
        )
        if not artifact.is_file():
            if artifact_is_superseded:
                continue
            raise CompilerWorkerV2Error(f"completed artifact readback mismatch: {artifact}")
        expected_hash = outcome_revision_hash(root, package, completion, portable_artifact_path) or accepted_change.get("sha256", item.get("sha256"))
        if (
            not receipt_artifact_matches(artifact, expected_hash)
            and not artifact_is_superseded
        ):
            if allow_derived_outcome_drift and portable_artifact_path == derived_outcome_path:
                continue
            raise CompilerWorkerV2Error(f"completed artifact readback mismatch: {artifact}")
    job = validate_job_package_binding(root, kp_path, kp)
    expected_receipt = completion_path.relative_to(root).as_posix()
    if job.get("status") == "committing" and not job.get("completion_receipt_path"):
        complete_v2_job(root, str(completion.get("job_id") or ""), completion_path)
        job = read_json(job_file(root, str(completion.get("job_id") or "")))
    if job.get("status") != "completed" or str(job.get("completion_receipt_path") or "") != expected_receipt:
        raise CompilerWorkerV2Error("completion receipt and v2 job state do not agree")
    if current_pointer and current_completion_path != this_completion_path:
        return {**completion, "superseded_by": current_pointer}
    return completion


def run_infographic_visual_qc(image: Path) -> dict[str, Any]:
    tool = Path(__file__).resolve().with_name("infographic_visual_qc.py")
    completed = subprocess.run(
        [sys.executable, str(tool), "--image", str(image), "--json"],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise CompilerWorkerV2Error("infographic visual QC returned invalid JSON") from exc
    if not isinstance(payload, dict):
        raise CompilerWorkerV2Error("infographic visual QC returned an invalid payload")
    return payload


def visual_blocking_failure(payload: dict[str, Any]) -> str:
    failed = payload.get("failed_checks") if isinstance(payload.get("failed_checks"), list) else []
    names = {
        str(item.get("name") or item)
        for item in failed
        if isinstance(item, (dict, str))
    }
    if any("footer" in name for name in names):
        return "missing_footer"
    if "gpt_image_2_size_constraints" in names:
        return "invalid_image_dimensions"
    return "cropped_content"


def clear_infographic_receipt(infographic: dict[str, Any]) -> None:
    for field in (
        "action_identity",
        "png_sha256",
        "dimensions",
        "visual_qc",
        "page_number_readback",
        "footer_text_readback",
        "semantic_readback",
        "generation_attempt_count",
        "generation_attempt_kind",
        "attempt_record_sha256",
        "attempt_ledger_path",
    ):
        infographic.pop(field, None)
    infographic["status"] = "awaiting_imagegen"
    infographic["standalone_content_status"] = "compiled_source_grounded"


def apply_attempt_result_to_kp(
    infographic: dict[str, Any],
    record: dict[str, Any],
    attempt: dict[str, Any],
) -> None:
    if attempt.get("status") != "accepted":
        clear_infographic_receipt(infographic)
        return
    infographic.update({
        "status": "receipt_verified",
        "provider": FORMAL_INFOGRAPHIC_PROVIDER,
        "model": FORMAL_INFOGRAPHIC_MODEL,
        "action_identity": attempt["action_identity"],
        "png_sha256": attempt["png_sha256"],
        "dimensions": attempt["dimensions"],
        "visual_qc": attempt["visual_qc"],
        "page_number_readback": attempt["page_number_readback"],
        "footer_text_readback": attempt["footer_text_readback"],
        "semantic_readback": attempt.get("semantic_readback", {}),
        "standalone_content_status": "passed",
        "generation_attempt_count": text_revision.count(record),
        "generation_attempt_kind": attempt["attempt_kind"],
        "attempt_record_sha256": attempt_record_sha256(record),
        "attempt_ledger_path": "imagegen_attempts.json",
    })


def contract_defect_reopen_state(
    package: Path,
    ledger: dict[str, Any],
    action: dict[str, Any],
) -> dict[str, Any] | None:
    """Validate the one permitted route for re-locking a defective prompt.

    A pending authorized action may be cancelled only when it has produced no
    PNG and every completed action is already a recorded contract defect.  The
    decision is shared by the preflight and the mutating reopen path so they
    cannot disagree about what can be reopened.
    """

    pages = ledger.get("pages")
    if not isinstance(pages, dict) or not pages:
        return None
    found_contract_defect = False
    cancelled_action: dict[str, str] | None = None
    for page_id, record in pages.items():
        if not isinstance(record, dict):
            return None
        attempts = record.get("attempts")
        if not isinstance(attempts, list) or not attempts:
            return None
        for index, attempt in enumerate(attempts):
            if not isinstance(attempt, dict):
                return None
            if (
                attempt.get("status") != "blocking_failure"
            ):
                is_latest_pending = (
                    attempt.get("status") == "authorized"
                    and index == len(attempts) - 1
                    and action.get("schema_version") == V2_IMAGEGEN_ACTION_ENVELOPE_VERSION
                    and action.get("payload_sha256") == canonical_payload_sha256(action)
                    and action.get("state") == "awaiting_imagegen"
                    and action.get("page_id") == page_id
                    and action.get("attempt_id") == attempt.get("attempt_id")
                    and action.get("action_identity") == attempt.get("action_identity")
                    and action.get("locked_prompt_sha256") == record.get("locked_prompt_sha256")
                    and str(action.get("expected_asset") or "").replace("\\", "/").endswith(
                        str(record.get("asset_path") or "").replace("\\", "/")
                    )
                )
                if not is_latest_pending or cancelled_action is not None:
                    return None
                try:
                    pending_asset = package_relative_path(
                        package,
                        str(record.get("asset_path") or ""),
                        "pending infographic asset",
                    )
                except CompilerWorkerV2Error:
                    return None
                if pending_asset.exists():
                    return None
                cancelled_action = {
                    "page_id": str(page_id),
                    "attempt_id": str(attempt.get("attempt_id") or ""),
                    "action_identity": str(attempt.get("action_identity") or ""),
                    "reason": "prompt_contract_defect_repair_before_generation",
                }
                continue
            if attempt.get("failure_code") not in INFOGRAPHIC_CONTRACT_DEFECT_FAILURES:
                return None
            found_contract_defect = True
    if not found_contract_defect:
        return None
    return {"cancelled_authorized_action": cancelled_action}


def contract_defect_reopen_is_allowed(package: Path, kp: dict[str, Any]) -> bool:
    if (package / "completion_receipt.json").exists():
        return False
    ledger_path = image_attempt_ledger_path(package)
    if not ledger_path.exists():
        return False
    source = kp.get("source") if isinstance(kp.get("source"), dict) else {}
    try:
        ledger = load_image_attempt_ledger(
            package,
            str(source.get("source_identity") or ""),
        )
    except CompilerWorkerV2Error:
        return False
    action_path = image_action_envelope_path(package)
    action = read_json(action_path) if action_path.exists() else {}
    return contract_defect_reopen_state(package, ledger, action) is not None


def archive_contract_defect_assets(
    package: Path,
    ledger: dict[str, Any],
    reopen_index: int,
) -> list[dict[str, str]]:
    """Copy prior rendered PNGs before their canonical asset paths are reused."""

    archive_dir = package / "assets" / f"contract_defect_reopen_{reopen_index:02d}"
    archived: list[dict[str, str]] = []
    pages = ledger.get("pages")
    assert isinstance(pages, dict)
    for page_id, record in pages.items():
        if not isinstance(record, dict):
            raise CompilerWorkerV2Error("contract defect ledger has an invalid page record")
        attempts = record.get("attempts")
        if not isinstance(attempts, list):
            raise CompilerWorkerV2Error("contract defect ledger has invalid attempts")
        rendered = next(
            (
                attempt
                for attempt in reversed(attempts)
                if isinstance(attempt, dict) and str(attempt.get("png_sha256") or "")
            ),
            None,
        )
        if rendered is None:
            continue
        asset_path = package_relative_path(
            package,
            str(record.get("asset_path") or ""),
            "contract-defect infographic asset",
        )
        if not asset_path.is_file():
            raise CompilerWorkerV2Error(
                f"contract-defect infographic asset is missing: {page_id}"
            )
        observed_sha256 = sha256_bytes(asset_path.read_bytes())
        if observed_sha256 != rendered.get("png_sha256"):
            raise CompilerWorkerV2Error(
                f"contract-defect infographic asset hash mismatch: {page_id}"
            )
        archive_path = archive_dir / asset_path.name
        if archive_path.exists():
            raise CompilerWorkerV2Error("contract-defect asset archive path already exists")
        archive_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(asset_path, archive_path)
        if sha256_bytes(archive_path.read_bytes()) != observed_sha256:
            raise CompilerWorkerV2Error("contract-defect asset archive readback mismatch")
        archived.append({
            "page_id": str(page_id),
            "original_asset_path": asset_path.relative_to(package).as_posix(),
            "archived_asset_path": archive_path.relative_to(package).as_posix(),
            "png_sha256": observed_sha256,
        })
    return archived


def reopen_contract_defect_infographic_attempts(
    root: Path,
    package: Path,
    kp_path: Path,
    kp: dict[str, Any],
) -> dict[str, Any] | None:
    """Archive failed text-contract attempts so a fixed compiler contract can re-lock."""

    if (package / "completion_receipt.json").exists():
        return None
    ledger_path = image_attempt_ledger_path(package)
    if not ledger_path.exists():
        return None
    source = kp.get("source") if isinstance(kp.get("source"), dict) else {}
    ledger = load_image_attempt_ledger(
        package,
        str(source.get("source_identity") or ""),
    )
    action_path = image_action_envelope_path(package)
    action = read_json(action_path) if action_path.exists() else {}
    reopen_state = contract_defect_reopen_state(package, ledger, action)
    if reopen_state is None:
        return None
    existing = sorted(package.glob("imagegen_attempts.contract_defect_reopen_*.json"))
    reopen_index = len(existing) + 1
    archive_path = package / f"imagegen_attempts.contract_defect_reopen_{reopen_index:02d}.json"
    action_archive_path = (
        package / f"image_action_envelope.contract_defect_reopen_{reopen_index:02d}.json"
        if action_path.exists()
        else None
    )
    prior_kp_sha256 = sha256_bytes(kp_path.read_bytes())
    prior_ledger_sha256 = sha256_bytes(ledger_path.read_bytes())
    prior_action_sha256 = sha256_bytes(action_path.read_bytes()) if action_path.exists() else ""
    archived_assets = archive_contract_defect_assets(package, ledger, reopen_index)
    ledger_path.replace(archive_path)
    if action_archive_path is not None:
        action_path.replace(action_archive_path)
    receipt = {
        "schema_version": INFOGRAPHIC_CONTRACT_DEFECT_REOPEN_SCHEMA_VERSION,
        "status": "reopened",
        "reason": "prompt_contract_defect_repair",
        "source_id": source.get("source_id"),
        "source_identity": source.get("source_identity"),
        "knowledge_package_path": kp_path.relative_to(root).as_posix(),
        "knowledge_package_sha256_before_reopen": prior_kp_sha256,
        "archived_attempt_ledger_path": archive_path.relative_to(package).as_posix(),
        "archived_attempt_ledger_sha256": prior_ledger_sha256,
        "archived_action_envelope_path": (
            action_archive_path.relative_to(package).as_posix()
            if action_archive_path is not None
            else ""
        ),
        "archived_action_envelope_sha256": prior_action_sha256,
        "archived_assets": archived_assets,
        "cancelled_authorized_action": reopen_state["cancelled_authorized_action"],
        "allowed_failure_codes": sorted(INFOGRAPHIC_CONTRACT_DEFECT_FAILURES),
        "reopened_at": iso_now(),
    }
    receipt["payload_sha256"] = canonical_payload_sha256(receipt)
    write_json(
        package / f"imagegen_contract_defect_reopen_{reopen_index:02d}.json",
        receipt,
    )
    return receipt


def ingest_plan_scope(job: dict[str, Any], kp: dict[str, Any]) -> dict[str, Any]:
    source = kp.get("source") or {}
    profile = job.get("source_profile", source.get("source_profile", "article"))
    if source.get("source_profile", profile) != profile:
        raise CompilerWorkerV2Error("ingest source_profile differs from accepted input")
    pages = kp.get("infographics") or []
    execution_policy(str(profile), len(pages))  # Reject unknown profiles and empty plans.
    scope = {
        "source_identity": source.get("source_identity"),
        "canonical_body_sha256": source.get("canonical_body_sha256"),
        "source_profile": profile,
        "coverage_sha256": canonical_payload_sha256({"units": kp.get("coverage_units")}),
        "coverage_units": {unit["id"]: canonical_payload_sha256(unit) for unit in kp.get("coverage_units", [])},
        "pages": [{
            "page_id": page.get("page_id"),
            "compiled_plan_sha256": page.get("compiled_plan_sha256"),
            "coverage_ids": page.get("coverage_ids", []),
            # Page count and numbering affect every compiled prompt when one
            # page is added. Compare authored content separately from that drift.
            "content_sha256": canonical_payload_sha256({key: page.get(key) for key in (
                "title", "title_source", "page_focus", "layout_archetype", "visible_text",
                "navigation_text", "content_use", "graph_type", "visual_structure",
                "source_anchors", "number_labels", "source_topic", "subtitle",
                "coverage_ids", "content_map",
            )}),
        } for page in pages],
    }
    if kp.get("semantic_plan_version") or job.get("semantic_plan_version"):
        scope["semantic_plan_version"] = kp.get("semantic_plan_version")
        scope["semantic_content_sha256"] = canonical_payload_sha256({
            key: kp.get(key) for key in ("source", "analysis", "knowledge", "relations", "application", "projection")
        })
    return scope


def case_execution_scope(job: dict[str, Any], kp: dict[str, Any], package: Path) -> dict[str, Any]:
    """The zero-imagegen equivalent of the formal locked ingestion plan."""
    source = kp.get("source") if isinstance(kp.get("source"), dict) else {}
    if source.get("asset_mode") != ASSET_MODE_CASE or kp.get("infographics"):
        raise CompilerWorkerV2Error("case execution scope requires a zero-infographic case package")
    errors = validate_case_evidence_kp(kp, package)
    if errors:
        raise CompilerWorkerV2Error("case execution lock blocked: " + "; ".join(errors))
    canonical = (package / "canonical_source.md").read_text(encoding="utf-8").rstrip("\n")
    if sha256_text(canonical) != source.get("canonical_body_sha256"):
        raise CompilerWorkerV2Error("case execution lock canonical hash mismatch")
    manifest = read_case_evidence_manifest(package, source)
    case_evidence = kp.get("case_evidence") if isinstance(kp.get("case_evidence"), dict) else {}
    return {
        "asset_mode": ASSET_MODE_CASE,
        "source_identity": source.get("source_identity"),
        "canonical_body_sha256": source.get("canonical_body_sha256"),
        "source_profile": source.get("source_profile", job.get("source_profile", "article")),
        "case_evidence_manifest_path": source.get("case_evidence_manifest_path"),
        "case_evidence_manifest_sha256": source.get("case_evidence_manifest_sha256"),
        "case_evidence_source_manifest_sha256": source.get("case_evidence_source_manifest_sha256"),
        "case_id": manifest.get("case_id"),
        "topic_id": manifest.get("topic_id"),
        "method_card_sha256": canonical_payload_sha256(case_evidence.get("method_card") or {}),
        "zero_imagegen": True,
        "infographic_page_count": 0,
    }


def require_case_ingest_plan(job: dict[str, Any], kp: dict[str, Any], package: Path) -> dict[str, Any]:
    contract = job.get("execution_contract")
    if not isinstance(contract, dict):
        raise CompilerWorkerV2Error("case_execution_plan_missing: submit the case semantic plan before formal write")
    scope = case_execution_scope(job, kp, package)
    if (
        contract.get("schema_version") != EXECUTION_CONTRACT_VERSION
        or contract.get("mode") != "case_evidence_zero_imagegen"
        or contract.get("scope") != scope
        or contract.get("scope_sha256") != canonical_payload_sha256(scope)
        or contract.get("first_accepted_at") != job.get("first_accepted_at")
        or contract.get("imagegen") != "prohibited"
    ):
        raise CompilerWorkerV2Error("case execution contract drifted")
    return contract


def lock_case_ingest_plan(job: dict[str, Any], kp: dict[str, Any], package: Path) -> None:
    scope = case_execution_scope(job, kp, package)
    previous = job.get("execution_contract")
    if previous:
        require_case_ingest_plan(job, kp, package)
        return
    job["execution_contract"] = {
        "schema_version": EXECUTION_CONTRACT_VERSION,
        "mode": "case_evidence_zero_imagegen",
        "first_accepted_at": job["first_accepted_at"],
        "plan_locked_at": iso_now(),
        "scope": scope,
        "scope_sha256": canonical_payload_sha256(scope),
        "imagegen": "prohibited",
        "stage_order": ["input_bundle", "locked_case_plan", "source_local_write_readback", "completed"],
        "normative_owner": "docs/Compiler Worker與Knowledge Package設計及驗收規格.md#7.2",
        "enforcement_boundary": "compiler entry points; not arbitrary shell, editor or model reasoning",
    }


def require_ingest_plan(job: dict[str, Any], kp: dict[str, Any]) -> dict[str, Any]:
    contract = job.get("execution_contract")
    if not isinstance(contract, dict):
        raise CompilerWorkerV2Error("ingest_execution_plan_missing: run compile-infographics before image generation or formal write")
    scope = ingest_plan_scope(job, kp)
    if contract.get("schema_version") != EXECUTION_CONTRACT_VERSION or contract.get("mode") != "content_fast_path":
        raise CompilerWorkerV2Error("invalid ingest execution contract")
    if contract.get("scope") != scope or contract.get("scope_sha256") != canonical_payload_sha256(scope):
        raise CompilerWorkerV2Error("ingest_plan_scope_changed: source, profile, coverage and page plan are locked; optional replanning is prohibited")
    if contract.get("first_accepted_at") != job.get("first_accepted_at"):
        raise CompilerWorkerV2Error("ingest first_accepted_at drift")
    policy = execution_policy(str(scope["source_profile"]), len(scope["pages"]))
    if any(contract.get(key) != value for key, value in policy.items()):
        raise CompilerWorkerV2Error("ingest execution policy drift")
    return contract


def ensure_ingest_plan(root: Path, job: dict[str, Any], kp: dict[str, Any], package: Path) -> bool:
    """Adopt pre-gate in-flight jobs once; never regenerate their existing images."""
    source = kp.get("source") if isinstance(kp.get("source"), dict) else {}
    if source.get("asset_mode") == ASSET_MODE_CASE:
        require_case_ingest_plan(job, kp, package)
        return False
    if job.get("execution_contract"):
        require_ingest_plan(job, kp)
        return False
    bundle_path = package / "input_bundle.json"
    bundle = read_json(bundle_path) if bundle_path.is_file() else {}
    if job.get("execution_policy_version") or bundle.get("execution_policy_version"):
        require_ingest_plan(job, kp)  # New jobs cannot opt out by deleting their lock.
    canonical = (package / "canonical_source.md").read_text(encoding="utf-8").rstrip("\n")
    if sha256_text(canonical) != kp["source"].get("canonical_body_sha256"):
        raise CompilerWorkerV2Error("legacy plan adoption canonical hash mismatch")
    errors = [error for page in kp.get("infographics", []) for error in validate_compiled_infographic_page(kp, package, page)]
    ledger_path = image_attempt_ledger_path(package)
    if ledger_path.is_file():
        ledger = load_image_attempt_ledger(package, kp["source"]["source_identity"])
        attempted = {**kp, "infographics": [page for page in kp["infographics"] if page.get("page_id") in ledger["pages"]]}
        action_path = image_action_envelope_path(package)
        action = read_json(action_path) if action_path.is_file() else {}
        pending_errors = set()
        for page_id, record in ledger["pages"].items():
            attempts = record.get("attempts") or []
            latest = attempts[-1] if attempts and isinstance(attempts[-1], dict) else {}
            status = latest.get("status")
            valid_pending = status == "blocking_failure" and latest.get("failure_code") in IMAGEGEN_BLOCKING_FAILURES
            if status == "authorized":
                valid_pending = (
                    action.get("schema_version") == V2_IMAGEGEN_ACTION_ENVELOPE_VERSION
                    and action.get("payload_sha256") == canonical_payload_sha256(action)
                    and action.get("state") == "awaiting_imagegen"
                    and action.get("source_identity") == kp["source"]["source_identity"]
                    and action.get("page_id") == page_id
                    and action.get("locked_prompt_sha256") == record.get("locked_prompt_sha256")
                    and all(latest.get(key) and latest.get(key) == action.get(key)
                            for key in ("attempt_id", "action_identity"))
                )
            if valid_pending:
                pending_errors.add(f"infographic {page_id} has no accepted final attempt")
        # Only explicitly verified pending records are resumable.
        errors.extend(error for error in validate_image_attempt_ledger(attempted, package)
                      if error not in pending_errors)
    if errors:
        raise CompilerWorkerV2Error("legacy plan adoption blocked: " + "; ".join(errors))
    lock_ingest_plan(job, kp, package)
    job["execution_contract"]["adopted_legacy_plan"] = True
    job["execution_policy_version"] = EXECUTION_CONTRACT_VERSION
    write_v2_job(root, job)
    return True


def lock_ingest_plan(job: dict[str, Any], kp: dict[str, Any], package: Path,
                     repair: dict[str, Any] | None = None, *, contract_reopen: bool = False) -> None:
    scope = ingest_plan_scope(job, kp)
    previous = job.get("execution_contract")
    if previous and (previous.get("scope_sha256") != canonical_payload_sha256(previous.get("scope") or {}) or previous.get("first_accepted_at") != job.get("first_accepted_at")):
        raise CompilerWorkerV2Error("stored ingest execution contract is inconsistent")
    if previous and previous.get("scope") == scope:
        require_ingest_plan(job, kp)
        if repair:
            raise CompilerWorkerV2Error("plan repair supplied for an unchanged plan")
        return
    if previous:
        if any(previous["scope"].get(key) != scope.get(key) for key in ("source_identity", "canonical_body_sha256", "source_profile")):
            raise CompilerWorkerV2Error("plan repair cannot change the accepted source or profile")
        ledger_path = image_attempt_ledger_path(package)
        ledger = read_json(ledger_path) if ledger_path.is_file() else {}
        has_attempt = any(page.get("attempts") for page in (ledger.get("pages") or {}).values())
        if contract_reopen:
            old = previous["scope"]
            if any(old.get(key) != scope.get(key) for key in ("source_identity", "canonical_body_sha256", "source_profile", "coverage_sha256")) or [p["page_id"] for p in old["pages"]] != [p["page_id"] for p in scope["pages"]]:
                raise CompilerWorkerV2Error("contract repair cannot expand the locked ingest scope")
            if [p["content_sha256"] for p in old["pages"]] != [p["content_sha256"] for p in scope["pages"]]:
                raise CompilerWorkerV2Error("contract repair cannot redesign the locked page content")
        else:
            if has_attempt or previous.get("plan_repair") or not isinstance(repair, dict):
                raise CompilerWorkerV2Error("ingest_plan_scope_changed: one source-grounded --plan-repair-file is required before any image attempt")
            if repair.get("previous_scope_sha256") != previous.get("scope_sha256"):
                raise CompilerWorkerV2Error("plan repair is not bound to the locked plan")
            if repair.get("failure_code") not in {"source_coverage_gap", "wrong_source_content"}:
                raise CompilerWorkerV2Error("plan repair requires a blocking source-content defect, not redesign")
            canonical = (package / "canonical_source.md").read_text(encoding="utf-8")
            evidence = str(repair.get("source_evidence") or "").strip()
            ids = repair.get("coverage_ids")
            units = {u.get("id"): u for u in kp.get("coverage_units", [])}
            if not evidence or evidence not in canonical or not isinstance(ids, list) or not ids or any(i not in units for i in ids):
                raise CompilerWorkerV2Error("plan repair requires canonical source evidence and affected coverage IDs")
            if not any(evidence in str(units[i].get("source_evidence", "")) for i in ids) or not str(repair.get("reader_impact") or "").strip():
                raise CompilerWorkerV2Error("plan repair evidence must support an affected unit and explain reader impact")
            old_units = previous["scope"]["coverage_units"]
            changed_units = {key for key in old_units.keys() | scope["coverage_units"].keys() if old_units.get(key) != scope["coverage_units"].get(key)}
            if not changed_units or changed_units != set(ids):
                raise CompilerWorkerV2Error("plan repair must name exactly the changed source-content units; layout-only replanning is prohibited")
            old_pages = {page["page_id"]: page for page in previous["scope"]["pages"]}
            new_pages = {page["page_id"]: page for page in scope["pages"]}
            if any(not set(page["coverage_ids"]) & changed_units for page in scope["pages"] if page["page_id"] not in old_pages):
                raise CompilerWorkerV2Error("new repair page is unrelated to the blocking content defect")
            unaffected = []
            for page_id, old_page in old_pages.items():
                new_page = new_pages.get(page_id)
                if not (set(old_page["coverage_ids"]) | set((new_page or {}).get("coverage_ids", []))) & changed_units:
                    if new_page is None or old_page["content_sha256"] != new_page["content_sha256"]:
                        raise CompilerWorkerV2Error("existing repair page is unrelated to the blocking content defect")
                    unaffected.append(page_id)
            if unaffected != [p["page_id"] for p in scope["pages"] if p["page_id"] in unaffected]:
                raise CompilerWorkerV2Error("plan repair cannot reorder unaffected pages")
    elif repair:
        raise CompilerWorkerV2Error("cannot repair a plan that has not been locked")
    contract = {
        **execution_policy(str(scope["source_profile"]), len(scope["pages"])),
        "first_accepted_at": job["first_accepted_at"],
        "plan_locked_at": previous["plan_locked_at"] if previous else iso_now(),
        "scope": scope,
        "scope_sha256": canonical_payload_sha256(scope),
    }
    if previous:
        contract["previous_scope_sha256"] = previous["scope_sha256"]
        if previous.get("adopted_legacy_plan"):
            contract["adopted_legacy_plan"] = True
        if previous.get("plan_repair"):
            contract["plan_repair"] = previous["plan_repair"]
        if repair:
            contract["plan_repair"] = {**repair, "recorded_at": iso_now()}
        if contract_reopen:
            contract["contract_reopen_at"] = iso_now()
    job["execution_contract"] = contract


def ingest_execution_summary(job: dict[str, Any], *, completed_at: str | None = None) -> dict[str, Any]:
    contract = job.get("execution_contract") or {}
    finish = completed_at or iso_now()
    milestones = [("input_preparation", job.get("input_ready_at")),
                  ("knowledge_and_plan", contract.get("plan_locked_at")),
                  ("image_generation_and_readback", job.get("images_ready_at")),
                  ("projection_and_readback", finish if completed_at else None)]
    def timestamp(value: str) -> datetime:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise CompilerWorkerV2Error("ingest timing requires timezone-aware timestamps")
        return parsed
    start = timestamp(str(job["first_accepted_at"]))
    end = timestamp(finish)
    if end < start:
        raise CompilerWorkerV2Error("ingest completion precedes acceptance")
    cursor = start
    stages = {}
    for name, raw in milestones:
        if raw:
            point = timestamp(str(raw))
            if point < cursor or point > end:
                raise CompilerWorkerV2Error("ingest timing milestones are out of order")
            stages[name] = round((point - cursor).total_seconds(), 3)
            cursor = point
        else:
            stages[name] = None
    if contract.get("adopted_legacy_plan"):
        stages = {name: None for name, _ in milestones}
    wall = round((end - start).total_seconds(), 3)
    target = contract.get("target_seconds")
    return {
        "scope_sha256": contract.get("scope_sha256"),
        "first_accepted_at": job["first_accepted_at"], "measured_at": finish,
        "wall_seconds": wall, "target_seconds": target,
        "target_status": "not_comparable" if target is None else ("over_target" if wall > target else "within_target"),
        "stage_wall_seconds": stages,
        "stage_timing_available": not bool(contract.get("adopted_legacy_plan")) and all(value is not None for value in stages.values()),
        "timing_scope": "observed stage intervals, including agent work/waits/readback; not provider-only time",
        "next_action": "stop" if completed_at else "resume_remaining_required_work_only",
        "quality_gate_waived": False,
    }


class InfographicPlanPreflightError(CompilerWorkerV2Error):
    def __init__(self, issues: list[dict[str, str]]) -> None:
        self.issues = issues
        super().__init__("infographic plan validation failed: " + "; ".join(issue["message"] for issue in issues))


def infographic_plan_preflight_issues(
    kp: dict[str, Any], package: Path, *, phase: str = "compile",
) -> list[dict[str, str]]:
    """Collect every actionable plan defect before prompts or image actions exist."""
    issues: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    pages = kp.get("infographics") if isinstance(kp.get("infographics"), list) else []
    units = kp.get("coverage_units") if isinstance(kp.get("coverage_units"), list) else []
    source = kp.get("source") if isinstance(kp.get("source"), dict) else {}
    canonical_path = package / "canonical_source.md"
    canonical_body = canonical_path.read_text(encoding="utf-8").rstrip("\n") if canonical_path.is_file() else ""

    def add(code: str, path: str, message: str, fix: str, *, page_id: str = "", coverage_id: str = "") -> None:
        key = (code, path, message)
        if key in seen:
            return
        seen.add(key)
        issues.append({
            "code": code, "path": path, "page_id": page_id,
            "coverage_id": coverage_id, "message": message, "fix": fix,
        })

    try:
        from tools.ingest_plan_contract import content_errors
    except ModuleNotFoundError:
        from ingest_plan_contract import content_errors
    for message in content_errors(kp, package):
        add("semantic_content", message.split(":", 1)[0], message, "Correct the typed semantic input before image generation.")
    if source.get("source_input_kind") == "user_provided_text":
        origin_hint = extract_user_text_origin_hint(canonical_body)
        citation_errors = validate_user_text_citation_evidence(source, required=bool(origin_hint))
        if origin_hint or isinstance(source.get("citation_evidence"), dict):
            citation_errors.extend(validate_user_text_external_origin(source))
        if origin_hint:
            citation_errors.extend(validate_user_text_origin_hint(source, origin_hint))
        for message in citation_errors:
            add("citation_evidence", "source.citation_evidence", message, "Verify and supply the cited work before compiling its footer.")

    if not canonical_body:
        add("canonical_source_missing", "canonical_source.md", "missing canonical_source.md", "Restore the accepted canonical source before planning.")
    elif source.get("canonical_body_sha256") != sha256_text(canonical_body):
        add("canonical_source_hash", "source.canonical_body_sha256", "canonical body hash mismatch", "Use the accepted canonical source and matching hash.")
    for path, message, fix in accepted_body_integrity_issues(kp, package):
        add("accepted_source_integrity", path, message, fix)
    if not pages:
        add("pages_missing", "infographics", "missing infographic plan", "Add source-grounded infographic pages before compiling.")

    allowed_statuses = (
        {"planned"} if phase == "plan"
        else {"planned", "awaiting_imagegen"} if phase == "compile"
        else {"awaiting_imagegen", "receipt_verified"}
    )
    page_for_unit: dict[str, str] = {}
    for index, page in enumerate(pages, start=1):
        if not isinstance(page, dict):
            add("invalid_page", f"infographics[{index}]", f"infographic {index} must be an object", "Replace the page with a complete plan object.")
            continue
        page_id = str(page.get("page_id") or f"page-{index}")
        prefix = f"infographics[{page_id}]"
        # Existing accepted PNG evidence is immutable; every new/changed page
        # must be understood before its next dispatch, including legacy-schema input.
        if page.get("status") != "receipt_verified":
            try:
                from tools.infographic_prompt_contract import reader_plan_review_errors
            except ModuleNotFoundError:
                from infographic_prompt_contract import reader_plan_review_errors
            for error in reader_plan_review_errors(page):
                add("reader_comprehension", f"{prefix}.reader_review", error,
                    "Give an independent reader only this page's text/question/structure; revise gaps, then bind the new readback hash.", page_id=page_id)
        for coverage_id in string_list(page.get("coverage_ids")):
            page_for_unit.setdefault(coverage_id, page_id)
        if page.get("status") not in allowed_statuses:
            expected = "planned" if phase == "plan" else "planned or awaiting_imagegen" if phase == "compile" else "awaiting_imagegen or receipt_verified"
            fix = "Set status: planned before compiling the new plan." if phase == "plan" else "Set the required page status before this stage; do not change an already authorized page."
            add("page_status", f"{prefix}.status", f"{page_id} status must be {expected}", fix, page_id=page_id)
        if not string_list(page.get("visible_text")):
            add("visible_text_missing", f"{prefix}.visible_text", f"{page_id} missing visible_text", "Generate a draft from reviewed infographic_text, then include every required visual-structure line.", page_id=page_id)
        subtitle = str(page.get("subtitle") or page.get("page_focus") or "").strip()
        quality_fields = [("subtitle", subtitle)] if subtitle else []
        quality_fields += [(f"visible_text[{i}]", line) for i, line in enumerate(string_list(page.get("visible_text")), start=1)]
        number_labels = string_list(page.get("number_labels"))
        if number_labels:
            quality_fields.append(("number_labels", "；".join(number_labels)))
        for field, line in quality_fields:
            for error in visible_text_quality_errors([line], contextual_labels=contextual_heading_labels(page)):
                add("text_not_self_contained", f"{prefix}.{field}", f"{page_id} {field}: {error}", "Rewrite as a short source-specific sentence with an object and a judgment, action, consequence, or use.", page_id=page_id)

    for unit in units:
        if not isinstance(unit, dict):
            continue
        coverage_id = str(unit.get("id") or "").strip()
        dispositions = unit.get("dispositions", unit.get("disposition", []))
        if isinstance(dispositions, str):
            infographic_bound = dispositions == "infographic"
        else:
            infographic_bound = isinstance(dispositions, list) and "infographic" in dispositions
        if not infographic_bound or not coverage_id:
            continue
        path = f"coverage_units[{coverage_id}].infographic_text"
        lines = string_list(unit.get("infographic_text"))
        if not lines:
            add("infographic_text_missing", path, f"coverage unit {coverage_id} lacks reviewed infographic_text", "Write source-grounded, self-contained reader text here before generating mappings.", page_id=page_for_unit.get(coverage_id, ""), coverage_id=coverage_id)
        owner = next((p for p in pages if isinstance(p, dict) and p.get("page_id") == page_for_unit.get(coverage_id)), {})
        for index, line in enumerate(lines, start=1):
            for error in visible_text_quality_errors([line], contextual_labels=contextual_heading_labels(owner)):
                add("text_not_self_contained", f"{path}[{index}]", f"coverage unit {coverage_id} infographic_text[{index}]: {error}", "Rewrite the reviewed text as an independently understandable sentence, then regenerate its mapping.", page_id=page_for_unit.get(coverage_id, ""), coverage_id=coverage_id)

    if canonical_body:
        contract_errors = [
            (message, "") for message in [
                *validate_primary_work_metadata(source, canonical_body),
                *validate_coverage_units(kp, canonical_body),
                *validate_coverage_audit(kp),
                *validate_content_map(units, pages, canonical_body),
                *validate_multi_page_orientation([page for page in pages if isinstance(page, dict)]),
            ]
        ]
        for page in pages:
            if isinstance(page, dict):
                page_id = str(page.get("page_id") or "")
                contract_errors.extend((message, page_id) for message in validate_reader_metadata(source, page, canonical_body))
                contract_errors.extend((message, page_id) for message in validate_visual_structure(page))
        for message, owning_page_id in contract_errors:
            if "lacks reviewed infographic_text" in message or message.startswith("multi-page orientation first page visible_text["):
                continue  # Already reported with an exact field and repair direction.
            page_match = re.search(r"(?:infographic|page) (\d+)", message)
            page_index = int(page_match.group(1)) - 1 if page_match else -1
            page = pages[page_index] if 0 <= page_index < len(pages) and isinstance(pages[page_index], dict) else {}
            page_id = owning_page_id or str(page.get("page_id") or "")
            coverage_match = re.search(r"coverage unit ([^\s]+)", message)
            coverage_id = coverage_match.group(1) if coverage_match else ""
            if not page_id and coverage_id:
                page_id = page_for_unit.get(coverage_id, "")
            if "content_map" in message:
                field = "content_map"
                fix = "Generate content_map from coverage_units[].infographic_text and source_evidence; review the result."
            elif "visible_text" in message or "visible text" in message:
                field = "visible_text"
                fix = "Make visible_text match every mapped and visual-structure text line exactly."
            elif "source evidence" in message:
                field = "source_evidence"
                fix = "Use a verbatim evidence span from canonical_source.md."
            elif "infographic_text" in message:
                field = "infographic_text"
                fix = "Review and complete the source-grounded reader sentence."
            elif "visual_structure" in message:
                field = "visual_structure"
                fix = "Complete the required graph-type structure and include its text in the reviewed mapping."
            else:
                field = "plan"
                fix = "Correct the named contract field, then rerun plan-preflight."
            path = f"infographics[{page_id}].{field}" if page_id else (f"coverage_units[{coverage_id}].{field}" if coverage_id else field)
            add("contract_mismatch", path, message, fix, page_id=page_id, coverage_id=coverage_id)
    return issues


def infographic_plan_text_template(kp: dict[str, Any]) -> list[dict[str, Any]]:
    """Derive draft mappings from reviewed coverage text without editing the KP."""
    units = {
        str(unit.get("id") or ""): unit
        for unit in kp.get("coverage_units") or []
        if isinstance(unit, dict)
    }
    templates: list[dict[str, Any]] = []
    for page in kp.get("infographics") or []:
        if not isinstance(page, dict):
            continue
        mappings = []
        missing = []
        for coverage_id in string_list(page.get("coverage_ids")):
            unit = units.get(coverage_id)
            if unit is None:
                missing.append(coverage_id)
                continue
            lines = string_list(unit.get("infographic_text"))
            if not lines:
                missing.append(coverage_id)
            mappings.append({"coverage_id": coverage_id, "source_evidence": str(unit.get("source_evidence") or ""), "visible_text": lines})
        visible = list(dict.fromkeys(line for row in mappings for line in row["visible_text"]))
        templates.append({"page_id": page.get("page_id"), "status": "planned", "coverage_ids": page.get("coverage_ids", []), "content_map": mappings, "visible_text": visible, "missing_reviewed_coverage_ids": missing})
    return templates


def command_plan_preflight(args: argparse.Namespace) -> int:
    root = args.root.resolve()
    kp_path = relative_to_root(args.kp_file, root, "Knowledge Package")
    kp = read_json(kp_path)
    issues = infographic_plan_preflight_issues(kp, kp_path.parent, phase=args.phase)
    print(json.dumps({"status": "ready" if not issues else "blocked", "stage": "plan_preflight", "phase": args.phase, "issues": issues, "write_performed": False, "image_authorization_allowed": not issues}, ensure_ascii=False))
    return 0 if not issues else 2


def command_plan_text_template(args: argparse.Namespace) -> int:
    root = args.root.resolve()
    kp_path = relative_to_root(args.kp_file, root, "Knowledge Package")
    print(json.dumps({"status": "draft_requires_review", "knowledge_package": str(kp_path), "pages": infographic_plan_text_template(read_json(kp_path)), "write_performed": False}, ensure_ascii=False))
    return 0


def command_compile_infographics(args: argparse.Namespace) -> int:
    root = args.root.resolve()
    kp_path = relative_to_root(args.kp_file, root, "Knowledge Package")
    if not kp_path.is_file():
        raise CompilerWorkerV2Error("Knowledge Package file is missing")
    package = kp_path.parent
    kp = read_json(kp_path)
    page_recovery = kp.get("incomplete_recovery") if isinstance(kp.get("incomplete_recovery"), dict) else None
    pending_contract_defect_reopen = False
    reopen_receipt = None
    if (package / "completion_receipt.json").exists():
        raise CompilerWorkerV2Error("completed package cannot compile another infographic plan")
    preflight_issues = infographic_plan_preflight_issues(kp, package)
    if preflight_issues:
        raise InfographicPlanPreflightError(preflight_issues)
    if image_action_envelope_path(package).exists():
        if page_recovery and read_json(image_action_envelope_path(package)).get("state") == "completed":
            pass
        elif contract_defect_reopen_is_allowed(package, kp):
            pending_contract_defect_reopen = True
        else:
            raise CompilerWorkerV2Error("infographic plan cannot change after image authorization")
    ledger_path = image_attempt_ledger_path(package)
    if ledger_path.exists():
        source_identity = str((kp.get("source") or {}).get("source_identity") or "")
        ledger = load_image_attempt_ledger(package, source_identity)
        if any(
            isinstance(page, dict) and page.get("attempts")
            for page in ledger.get("pages", {}).values()
        ):
            if page_recovery:
                pass
            elif contract_defect_reopen_is_allowed(package, kp):
                pending_contract_defect_reopen = True
            else:
                raise CompilerWorkerV2Error("infographic plan cannot change after an image attempt")

    job = validate_job_package_binding(root, kp_path, kp)
    source = kp.get("source") if isinstance(kp.get("source"), dict) else {}
    canonical_path = package / "canonical_source.md"
    if not canonical_path.is_file():
        raise CompilerWorkerV2Error("missing canonical_source.md")
    canonical_body = canonical_path.read_text(encoding="utf-8").rstrip("\n")
    if source.get("canonical_body_sha256") != sha256_text(canonical_body):
        raise CompilerWorkerV2Error("canonical body hash mismatch")
    infographics = [
        item for item in kp.get("infographics") or [] if isinstance(item, dict)
    ]
    if not infographics:
        raise CompilerWorkerV2Error("missing infographic plan")
    invalid_statuses = [
        str(item.get("page_id") or "")
        for item in infographics
        if item.get("status") not in ({"planned", "awaiting_imagegen", "receipt_verified"} if page_recovery else {"planned", "awaiting_imagegen"})
    ]
    if invalid_statuses:
        raise CompilerWorkerV2Error(
            "only planned infographic pages can be compiled: "
            + ", ".join(invalid_statuses)
        )
    plan_errors = [
        *validate_primary_work_metadata(source, canonical_body),
        *validate_coverage_units(kp, canonical_body),
        *validate_coverage_audit(kp),
        *validate_content_map(
            kp.get("coverage_units") or [],
            infographics,
            canonical_body,
        ),
        *validate_multi_page_orientation(infographics),
        *[
            error
            for infographic in infographics
            for error in validate_reader_metadata(source, infographic, canonical_body)
        ],
        *[
            error
            for infographic in infographics
            for error in validate_visual_structure(infographic)
        ],
    ]
    if plan_errors:
        raise CompilerWorkerV2Error(
            "infographic plan validation failed: " + "; ".join(plan_errors)
        )
    mutable_infographics = [item for item in infographics if item.get("status") != "receipt_verified"]
    if not mutable_infographics:
        raise CompilerWorkerV2Error("page-scoped recovery has no recoverable page")
    for sibling in (item for item in infographics if item.get("status") == "receipt_verified"):
        sibling_errors = validate_compiled_infographic_page(kp, package, sibling)
        if sibling_errors:
            raise CompilerWorkerV2Error("accepted sibling drifted: " + "; ".join(sibling_errors))
    generated_on = (str(mutable_infographics[0].get("generated_on") or date.today().isoformat())
                    if job.get("execution_contract") else date.today().isoformat())
    for infographic in mutable_infographics:
        if source.get("primary_work_status") != "primary":
            infographic["title"] = reader_infographic_title(infographic.get("title"))
        infographic["generated_on"] = generated_on
    compiled_pages = [
        compiled_infographic_page(kp, package, infographic)
        for infographic in mutable_infographics
    ]
    prompt_payloads: list[tuple[Path, str]] = []
    for infographic, compiled in zip(mutable_infographics, compiled_pages):
        index = infographics.index(infographic) + 1
        prompt_path = package / "prompts" / f"ig-{index}.txt"
        prompt_payloads.append((prompt_path, str(compiled["prompt"])))
        infographic.update({
            "status": "awaiting_imagegen",
            "provider": FORMAL_INFOGRAPHIC_PROVIDER,
            "model": FORMAL_INFOGRAPHIC_MODEL,
            "prompt_compiler": V2_INFOGRAPHIC_PROMPT_COMPILER,
            "prompt_contract_version": compiled["prompt_contract_version"],
            "compiled_plan_schema_version": compiled["schema_version"],
            "compiled_plan_sha256": compiled["compiled_plan_sha256"],
            "template_version": V2_INFOGRAPHIC_TEMPLATE_VERSION,
            "audience": INFOGRAPHIC_STANDALONE_AUDIENCE,
            "content_scope": "source_only",
            "standalone_content_status": "compiled_source_grounded",
            "generated_on": generated_on,
            "locked_prompt_path": prompt_path.relative_to(package).as_posix(),
            "locked_prompt_sha256": sha256_text(str(compiled["prompt"])),
            "asset_path": compiled["asset_path"],
        })
        if source.get("primary_work_status") == "primary":
            infographic["primary_work_title"] = source.get("primary_work_title")
        else:
            infographic.pop("primary_work_title", None)

    prior_kp = kp_path.read_bytes()
    repair_path = getattr(args, "plan_repair_file", None)
    repair = read_json(relative_to_root(repair_path, root, "plan repair evidence")) if repair_path else None
    lock_ingest_plan(job, kp, package, repair, contract_reopen=pending_contract_defect_reopen)
    if pending_contract_defect_reopen:
        reopen_receipt = reopen_contract_defect_infographic_attempts(root, package, kp_path, read_json(kp_path))
        if reopen_receipt is None:
            raise CompilerWorkerV2Error("contract defect reopen is no longer available")
        job.pop("images_ready_at", None)
    prior_prompts = {
        path: path.read_bytes() if path.exists() else None
        for path, _ in prompt_payloads
    }
    for prompt_path, prompt in prompt_payloads:
        prompt_path.parent.mkdir(parents=True, exist_ok=True)
        prompt_path.write_text(prompt + "\n", encoding="utf-8")
    write_json(kp_path, kp)
    write_performed = (
        kp_path.read_bytes() != prior_kp
        or any(
            path.read_bytes() != prior_prompts[path]
            for path, _ in prompt_payloads
        )
    )
    if write_performed or not read_json(job_file(root, job["job_id"])).get("execution_contract"):
        job.update({"status": "running", "updated_at": iso_now()})
        write_v2_job(root, job)
    print(json.dumps({
        "status": "compiled",
        "pages": [
            {
                "page_id": infographic.get("page_id"),
                "compiled_plan_sha256": compiled["compiled_plan_sha256"],
                "locked_prompt_path": infographic.get("locked_prompt_path"),
                "asset_path": infographic.get("asset_path"),
            }
            for infographic, compiled in zip(mutable_infographics, compiled_pages)
        ],
        "write_performed": write_performed,
        "contract_defect_reopen": reopen_receipt,
        "execution": ingest_execution_summary(job),
        "next_action": "authorize each compiled page with imagegen-authorize",
    }, ensure_ascii=False))
    return 0


def validate_visual_repair_authorization(package, kp, infographic, first, authorization):
    if not isinstance(authorization, dict):
        raise CompilerWorkerV2Error("explicit visual repair authorization is required")
    expected = {"schema_version": "explicit_visual_repair_v1", "authority": "current_user_message",
        "source_id": kp["source"]["source_id"], "page_id": infographic["page_id"],
        "first_attempt_id": first["attempt_id"], "reference_png_sha256": first["png_sha256"],
        "locked_prompt_sha256": infographic["locked_prompt_sha256"],
        "scope": "remove_excess_icons_preserve_all_text", "additional_attempts": 1}
    if any(authorization.get(key) != value for key, value in expected.items()):
        raise CompilerWorkerV2Error("visual repair authorization scope or identity mismatch")
    if first.get("failure_code") != "reverse_review_failed" or first.get("status") != "blocking_failure":
        raise CompilerWorkerV2Error("visual repair must retain the rejected first attempt")
    if not str(authorization.get("user_instruction") or "").strip() or not str(authorization.get("remove_icon_description") or "").strip():
        raise CompilerWorkerV2Error("visual repair requires the explicit instruction and exact icon target")
    reference_value = authorization.get("preserved_reference_asset") or authorization.get("reference_asset", "")
    reference = package_relative_path(package, reference_value, "repair original image")
    expected_hash = first["png_sha256"]
    if reference.is_file() and sha256_bytes(reference.read_bytes()) == expected_hash:
        return reference
    legacy_reference = package_relative_path(package, authorization.get("reference_asset", ""), "repair original image")
    legacy_archive = visual_repair_reference_archive_path(legacy_reference, first)
    if legacy_archive.is_file() and sha256_bytes(legacy_archive.read_bytes()) == expected_hash:
        return legacy_archive
    raise CompilerWorkerV2Error("visual repair original image bytes are not preserved")


def visual_repair_reference_archive_path(reference: Path, first: dict[str, Any]) -> Path:
    """Keep the rejected original byte-stable while its output path is replaced."""
    attempt_number = int(first.get("attempt_number") or 1)
    return reference.with_name(f"{reference.stem}.attempt-{attempt_number}-reference{reference.suffix}")


def preserve_visual_repair_reference(package: Path, reference: Path, first: dict[str, Any], authorization: dict[str, Any]) -> tuple[dict[str, Any], Path]:
    """Archive the rejected original before an edit can replace its asset path."""
    archive = visual_repair_reference_archive_path(reference, first)
    expected_hash = first["png_sha256"]
    if archive.exists():
        if not archive.is_file() or sha256_bytes(archive.read_bytes()) != expected_hash:
            raise CompilerWorkerV2Error("visual repair reference archive is invalid")
    else:
        archive.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(reference, archive)
        if sha256_bytes(archive.read_bytes()) != expected_hash:
            raise CompilerWorkerV2Error("visual repair reference archive hash mismatch")
    preserved = dict(authorization)
    preserved["preserved_reference_asset"] = archive.relative_to(package).as_posix()
    return preserved, archive



def blocking_repair_contract(root, asset, first, prompt, failure_code, authorization=None):
    """Bind concrete first-attempt review to an immutable reference before using a slot."""
    review = first.get("invalidation_readback", first.get("semantic_readback"))
    if not isinstance(review, dict):
        raise CompilerWorkerV2Error("blocking repair requires bound independent review")
    png_hash = first.get("png_sha256")
    if (review.get("inspection_method") != "independent_image_readback"
            or len(str(review.get("inspector_identity") or "").strip()) < 3
            or review.get("inspected_png_sha256") != png_hash
            or review.get("inspected_prompt_sha256") != sha256_text(prompt)
            or not asset.is_file() or sha256_bytes(asset.read_bytes()) != png_hash):
        raise CompilerWorkerV2Error("blocking repair review/reference binding mismatch")
    unexpected = review.get("unexpected_visible_text")
    extra_text = (failure_code in {"wrong_or_missing_locked_text", "internal_process_pollution"}
                  and isinstance(unexpected, list) and bool(unexpected)
                  and all(isinstance(t, str) and t.strip()
                          and comparable_visible_text(t) not in {comparable_visible_text(v) for v in visible_text_from_prompt(prompt)}
                          for t in unexpected)
                  and review.get("formal_ingestion_allowed") is False
                  and isinstance(review.get("review_axes"), dict)
                  and "failed" in review["review_axes"].values()
                  and len(str(review.get("reader_review") or "").strip()) >= 12)
    if not extra_text:
        errors = image_severe_failure_readback_errors(
            review, png_sha256=png_hash, prompt=prompt, failure_code=failure_code)
        if errors:
            raise CompilerWorkerV2Error("blocking repair lacks concrete content findings: " + "; ".join(errors))
    footer = footer_exception_text(authorization, prompt)
    suffix = ".blocking-repair-" + png_hash[:16] + "-reference.png" if footer else ".blocking-repair-reference.png"
    reference = relative_to_root(asset.with_name(asset.stem + suffix), root, "repair reference")
    if reference.exists():
        if sha256_bytes(reference.read_bytes()) != png_hash:
            raise CompilerWorkerV2Error("blocking repair reference archive conflict")
    else:
        with reference.open("xb") as stream:
            stream.write(asset.read_bytes())
    edit_prompt = blocking_repair_prompt(prompt, review, failure_code, footer_override=footer)
    return {"edit_prompt": edit_prompt, "edit_prompt_sha256": sha256_text(edit_prompt),
            "referenced_image_path": reference.relative_to(root).as_posix(),
            "reference_png_sha256": png_hash,
            "repair_review_sha256": sha256_text(json.dumps(review, ensure_ascii=False, sort_keys=True)),
            "imagegen_tool_arguments": {"prompt": edit_prompt,
                "referenced_image_paths": [str(reference)], "transparent_background": False}}


def blocking_repair_receipt_errors(second, first, prompt):
    saved = second.get("repair_dispatch_contract")
    review = first.get("invalidation_readback", first.get("semantic_readback"))
    if not isinstance(saved, dict) or not isinstance(review, dict):
        return ["blocking repair lacks saved dispatch contract"]
    footer = footer_exception_text(second.get("dispatch_correction_authorization"), prompt)
    expected = blocking_repair_prompt(prompt, review, first.get("failure_code", ""), footer_override=footer)
    if (saved.get("edit_prompt") != expected
            or saved.get("edit_prompt_sha256") != sha256_text(expected)
            or saved.get("repair_review_sha256") != sha256_text(json.dumps(review, ensure_ascii=False, sort_keys=True))
            or saved.get("reference_png_sha256") != first.get("png_sha256")
            or second.get("imagegen_tool_arguments") != saved.get("imagegen_tool_arguments")
            or (saved.get("imagegen_tool_arguments") or {}).get("prompt") != expected):
        return ["blocking repair receipt does not preserve the reviewed edit dispatch"]
    if footer and second.get("status") == "accepted":
        restricted = (second.get("semantic_readback") or {}).get("restricted_footer_review", {})
        if (restricted.get("only_footer_changed") is not True
                or restricted.get("reference_png_sha256") != first.get("png_sha256")):
            return ["footer exception lacks independent preservation readback"]
    return []


def validate_blocking_repair_dispatch(root, action, response):
    contract = action.get("imagegen_tool_arguments")
    prompt = action.get("edit_prompt")
    if (not isinstance(contract, dict) or not prompt
            or action.get("edit_prompt_sha256") != sha256_text(prompt)
            or contract.get("prompt") != prompt
            or response.get("imagegen_tool_arguments") != contract):
        raise CompilerWorkerV2Error("blocking repair actual dispatch must match the bound edit prompt and reference")
    reference = relative_to_root(root / action["referenced_image_path"], root, "repair reference")
    if (contract != {"prompt": prompt, "referenced_image_paths": [str(reference)], "transparent_background": False}
            or not reference.is_file() or sha256_bytes(reference.read_bytes()) != action.get("reference_png_sha256")):
        raise CompilerWorkerV2Error("blocking repair reference archive changed")


def footer_variance_binding_errors(readback, kp, page_id, action_identity):
    auth = readback.get("user_approved_footer_variance") if isinstance(readback, dict) else None
    if auth is None:
        return []
    expected = {"job_id": kp.get("job_id"), "source_identity": kp.get("source", {}).get("source_identity"),
                "page_id": page_id, "action_identity": action_identity}
    if not isinstance(auth, dict) or any(auth.get(k) != v for k, v in expected.items()):
        return ["footer separator acceptance is bound to another job or image action"]
    return []


def footer_exception_text(authorization, prompt):
    if not isinstance(authorization, dict) or authorization.get("schema") != "imagegen_footer_repair_authorization_v1":
        return None
    footer = visible_text_from_prompt(prompt)[-1]
    if authorization.get("replacement_footer") != footer:
        raise CompilerWorkerV2Error("footer exception must preserve the exact locked footer")
    return footer


def footer_exception_authorization_errors(kp, page_id, prior, authorization):
    if (not isinstance(prior, list) or len(prior) != 2
            or any(not isinstance(a, dict) or a.get("status") != "blocking_failure"
                   or a.get("failure_code") != "wrong_or_missing_locked_text" for a in prior)
            or prior[1].get("attempt_kind") != "blocking_repair"
            or not isinstance(prior[1].get("repair_dispatch_contract"), dict)):
        return ["footer exception requires two retained failures and the actual second repair dispatch"]
    expected = {"schema": "imagegen_footer_repair_authorization_v1",
                "scope": "one_additional_footer_repair", "authorized_by": "user",
                "job_id": kp.get("job_id"), "source_identity": kp.get("source", {}).get("source_identity"),
                "page_id": page_id, "failed_action_identity": prior[1].get("action_identity"),
                "failed_png_sha256": prior[1].get("png_sha256"),
                "previous_attempts_sha256": sha256_text(json.dumps(prior, ensure_ascii=False, sort_keys=True)),
                "diagnosis_code": "footer_edit_wrong_location", "preserve_all_other_content": True}
    if (any(authorization.get(k) != v for k, v in expected.items())
            or len(str(authorization.get("user_request") or "").strip()) < 6
            or not str(authorization.get("replacement_footer") or "").strip()):
        return ["footer exception authorization does not bind the same job, failures and user request"]
    return []


def dispatch_correction_authorization_errors(kp, page_id, prior, authorization):
    """Explicitly authorized dispatch correction; never resets the original attempts."""
    if isinstance(authorization, dict) and authorization.get("schema") == "imagegen_footer_repair_authorization_v1":
        return footer_exception_authorization_errors(kp, page_id, prior, authorization)
    if (not isinstance(prior, list) or len(prior) != 2
            or any(not isinstance(a, dict) or a.get("status") != "blocking_failure" for a in prior)
            or prior[1].get("attempt_kind") != "blocking_repair"
            or prior[1].get("repair_dispatch_contract") is not None
            or prior[1].get("failure_code") not in IMAGEGEN_REPAIRABLE_FAILURES
            or prior[0].get("failure_code") != prior[1].get("failure_code")):
        return ["dispatch correction requires two retained failures and an omitted legacy repair dispatch"]
    if not isinstance(authorization, dict):
        return ["dispatch correction requires explicit user exception authorization"]
    expected = {"schema": "imagegen_dispatch_correction_authorization_v1",
                "scope": "one_additional_dispatch", "authorized_by": "user",
                "job_id": kp.get("job_id"), "source_identity": kp.get("source", {}).get("source_identity"),
                "page_id": page_id, "failed_action_identity": prior[1].get("action_identity"),
                "failed_png_sha256": prior[1].get("png_sha256"),
                "previous_attempts_sha256": sha256_text(json.dumps(prior, ensure_ascii=False, sort_keys=True)),
                "diagnosis_code": "repair_instruction_omitted"}
    if (any(authorization.get(k) != v for k, v in expected.items())
            or len(str(authorization.get("user_request") or "").strip()) < 6):
        return ["dispatch correction authorization does not bind the original job, failures and user request"]
    return []


def command_imagegen_authorize(args: argparse.Namespace) -> int:
    root = args.root.resolve()
    kp_path = relative_to_root(args.kp_file, root, "Knowledge Package")
    if not kp_path.is_file():
        raise CompilerWorkerV2Error("Knowledge Package file is missing")
    package = kp_path.parent
    if (package / "completion_receipt.json").exists():
        raise CompilerWorkerV2Error("completed package cannot authorize another image generation")
    kp = read_json(kp_path)
    text_revision.recover_action(sys.modules[__name__], package)
    preflight_issues = infographic_plan_preflight_issues(kp, package, phase="authorize")
    if preflight_issues:
        raise InfographicPlanPreflightError(preflight_issues)
    job = validate_job_package_binding(root, kp_path, kp)
    adopted_execution = ensure_ingest_plan(root, job, kp, package)
    source = kp.get("source") if isinstance(kp.get("source"), dict) else {}
    source_identity = str(source.get("source_identity") or "")
    infographic = infographic_page(kp, args.page_id)
    compile_errors = validate_compiled_infographic_page(kp, package, infographic)
    if compile_errors:
        raise CompilerWorkerV2Error(
            "infographic page is not the current compiled result: "
            + "; ".join(compile_errors)
        )
    prompt_path, asset_path, lock_hash = locked_infographic_context(package, infographic)
    ledger = load_image_attempt_ledger(package, source_identity, create=True)
    pages = ledger["pages"]
    assert isinstance(pages, dict)
    page_id = str(infographic["page_id"])
    record = pages.get(page_id)
    if record is None:
        record = {
            "page_id": page_id,
            "locked_prompt_sha256": lock_hash,
            "asset_path": asset_path.relative_to(package).as_posix(),
            "attempts": [],
        }
        pages[page_id] = record
    if (
        not isinstance(record, dict)
        or record.get("locked_prompt_sha256") != lock_hash
        or record.get("asset_path") != asset_path.relative_to(package).as_posix()
        or not isinstance(record.get("attempts"), list)
    ):
        raise CompilerWorkerV2Error("image attempt history does not match the locked page")
    attempts = record["attempts"]
    assert isinstance(attempts, list)
    action_path = image_action_envelope_path(package)
    if action_path.exists():
        existing = read_json(action_path)
        if (
            existing.get("schema_version") == V2_IMAGEGEN_ACTION_ENVELOPE_VERSION
            and existing.get("payload_sha256") != canonical_payload_sha256(existing)
        ):
            raise CompilerWorkerV2Error("image action envelope hash mismatch")
        latest = attempts[-1] if attempts else None
        if (
            existing.get("schema_version") == V2_IMAGEGEN_ACTION_ENVELOPE_VERSION
            and existing.get("state") == "awaiting_imagegen"
            and existing.get("page_id") == page_id
            and existing.get("locked_prompt_sha256") == lock_hash
            and existing.get("reason") == args.reason
            and existing.get("failure_code", "") == ("reverse_review_failed" if args.reason == "user_authorized_visual_repair" else (args.failure or ""))
            and isinstance(latest, dict)
            and latest.get("status") == "authorized"
            and latest.get("action_identity") == existing.get("action_identity")
        ):
            if args.reason == "blocking_repair" and not existing.get("imagegen_tool_arguments"):
                raise CompilerWorkerV2Error("pending legacy repair lacks bound edit instructions; preserve attempt and reconcile")
            print(json.dumps({
                **existing,
                "status": "awaiting_imagegen",
                "write_performed": adopted_execution,
                "execution_contract_adopted": adopted_execution,
            }, ensure_ascii=False))
            return 0
        if existing.get("state") == "awaiting_imagegen":
            raise CompilerWorkerV2Error("another image generation action is still awaiting completion")
    is_text_revision = (kp.get("revision") or {}).get("mode") == "same_job_text_revision"
    if is_text_revision and args.reason != "user_authorized_text_revision":
        raise CompilerWorkerV2Error("same-job text revision requires its explicit revision action")
    if args.reason == "user_authorized_text_revision":
        if not is_text_revision or args.failure:
            raise CompilerWorkerV2Error("text revision requires a prepared same-job revision")
        auth_path = getattr(args, 'authorization_file', None)
        text_authorization = read_json(relative_to_root(auth_path, root, 'text correction authorization')) if auth_path else None
        revision_dispatch = text_revision.authorize(sys.modules[__name__], root, package, kp, infographic, record, text_authorization)
        attempt_kind = "user_authorized_text_revision"
        failure_code = ""
    elif args.reason == "initial":
        if args.failure:
            raise CompilerWorkerV2Error("initial_generation cannot carry a repair failure code")
        if attempts:
            raise CompilerWorkerV2Error("initial_generation cannot regenerate an attempted infographic page")
        attempt_kind = "initial_generation"
        failure_code = ""
    elif args.reason == "contract_defect_repair":
        if args.failure:
            raise CompilerWorkerV2Error("contract_defect_repair cannot carry a repair failure code")
        if attempts:
            raise CompilerWorkerV2Error("contract_defect_repair must start after archived failed attempts")
        if not sorted(package.glob("imagegen_contract_defect_reopen_*.json")):
            raise CompilerWorkerV2Error("contract_defect_repair requires a contract defect reopen receipt")
        attempt_kind = "contract_defect_repair"
        failure_code = ""
    elif args.reason == "user_authorized_visual_repair":
        if len(attempts) != 1 or args.failure:
            raise CompilerWorkerV2Error("explicit visual repair is limited to one second attempt")
        auth_path = getattr(args, "authorization_file", None)
        if not auth_path:
            raise CompilerWorkerV2Error("explicit visual repair requires --authorization-file")
        authorization = read_json(relative_to_root(auth_path, root, "visual repair authorization"))
        reference = validate_visual_repair_authorization(package, kp, infographic, attempts[0], authorization)
        authorization, reference = preserve_visual_repair_reference(
            package, reference, attempts[0], authorization
        )
        attempt_kind = "user_authorized_visual_repair"
        failure_code = "reverse_review_failed"
    else:
        if args.failure not in IMAGEGEN_REPAIRABLE_FAILURES:
            raise CompilerWorkerV2Error(
                "blocking_repair is allowed only for a severe content error or omission"
            )
        correction_authorization = None
        if args.reason == "dispatch_correction":
            auth_path = getattr(args, "authorization_file", None)
            if not auth_path:
                raise CompilerWorkerV2Error("dispatch correction requires --authorization-file")
            correction_authorization = read_json(relative_to_root(auth_path, root, "dispatch correction authorization"))
            errors = dispatch_correction_authorization_errors(kp, page_id, attempts, correction_authorization)
            if errors:
                raise CompilerWorkerV2Error("; ".join(errors))
        elif len(attempts) >= int(IMAGEGEN_GENERATION_POLICY["max_candidates_per_asset"]):
            raise CompilerWorkerV2Error("infographic page already used its one blocking repair")
        latest = attempts[-1] if attempts else None
        if (
            not isinstance(latest, dict)
            or len(attempts) != (2 if correction_authorization else 1)
            or latest.get("status") != "blocking_failure"
            or latest.get("failure_code") != args.failure
        ):
            raise CompilerWorkerV2Error("blocking_repair is not backed by the recorded first-attempt failure")
        repair_contract = blocking_repair_contract(
            root, asset_path, latest, prompt_path.read_text(encoding="utf-8").rstrip("\n"), str(args.failure), correction_authorization)
        attempt_kind = "blocking_repair"
        failure_code = str(args.failure)
    attempt_number = text_revision.count(record) + 1
    action_identity = sha256_text(
        f"{source_identity}|{page_id}|{lock_hash}|{attempt_number}|{uuid.uuid4().hex}"
    )
    attempt = {
        "attempt_id": f"IMGATT-{uuid.uuid4().hex}",
        "attempt_number": attempt_number,
        "attempt_kind": attempt_kind,
        "failure_code": failure_code,
        "status": "authorized",
        "authorized_at": iso_now(),
        "action_identity": action_identity,
    }
    if attempt_kind == "user_authorized_visual_repair":
        attempt["repair_authorization"] = authorization
    if attempt_kind == "blocking_repair":
        attempt["repair_dispatch_contract"] = repair_contract
        if correction_authorization:
            attempt["dispatch_correction_authorization"] = correction_authorization
    if attempt_kind == "user_authorized_text_revision":
        attempt["text_revision_dispatch"] = revision_dispatch
    attempts.append(attempt)
    if attempt_kind != "user_authorized_text_revision":
        write_json(image_attempt_ledger_path(package), ledger)
    envelope = {
        "schema_version": V2_IMAGEGEN_ACTION_ENVELOPE_VERSION,
        "state": "awaiting_imagegen",
        "requested_at": iso_now(),
        "job_id": kp.get("job_id"),
        "source_id": source.get("source_id"),
        "source_identity": source_identity,
        "page_id": page_id,
        "provider": FORMAL_INFOGRAPHIC_PROVIDER,
        "model": FORMAL_INFOGRAPHIC_MODEL,
        "generation_request": FORMAL_INFOGRAPHIC_GENERATION_REQUEST,
        "generation_contract": INFOGRAPHIC_GENERATION_CONTRACT,
        "generation_policy": IMAGEGEN_GENERATION_POLICY,
        "attempt_id": attempt["attempt_id"],
        "attempt_number": attempt_number,
        "attempt_kind": attempt_kind,
        "action_identity": action_identity,
        "reason": args.reason,
        "failure_code": failure_code,
        "locked_prompt_sha256": lock_hash,
        "prompt": prompt_path.read_text(encoding="utf-8").rstrip("\n"),
        "expected_asset": asset_path.relative_to(root).as_posix(),
        "response_contract": {
            "schema_version": V2_IMAGEGEN_RESPONSE_VERSION,
            "action_identity": "must match this envelope",
            "attempt_id": "must match this envelope",
            "provider": FORMAL_INFOGRAPHIC_PROVIDER,
            "model": FORMAL_INFOGRAPHIC_MODEL,
            "generation_request": "must exactly match this envelope",
            "runtime_tool": "must identify Codex imagegen as the producing tool",
            "asset_path": "must equal expected_asset",
            "png_sha256": "must equal the read-back PNG SHA-256",
            "outcome": "accepted or blocking_failure",
            "semantic_readback": (
                "required for accepted current-contract images; must bind the PNG and "
                "prompt hashes and reproduce the locked visible-text whitelist"
            ),
        },
    }
    if attempt_kind == "blocking_repair":
        envelope.update(repair_contract)
        if correction_authorization:
            envelope["dispatch_correction_authorization"] = correction_authorization
    if attempt_kind == "user_authorized_visual_repair":
        envelope["repair_authorization"] = authorization
        envelope["referenced_image_path"] = reference.relative_to(root).as_posix()
        envelope["edit_prompt"] = (
            "Edit the supplied original PNG in place with one strictly limited change: remove only "
            + authorization["remove_icon_description"]
            + ". Fill its former area with the same clean background. Preserve every other illustration, all text, "
              "punctuation, dates, line breaks, footer signature, page number, positions, colors, and canvas size. "
              "Do not redesign, reflow, translate, add, or omit any text. Preserve the original footer date even if today's date differs. "
              "The following original locked content remains authoritative for text; do not apply its layout to redesign the image.\n"
            + envelope["prompt"])
        envelope["edit_prompt_sha256"] = sha256_text(envelope["edit_prompt"])
    if attempt_kind == "user_authorized_text_revision":
        envelope.update(revision_dispatch)
    envelope["payload_sha256"] = canonical_payload_sha256(envelope)
    if attempt_kind == "user_authorized_text_revision":
        text_revision.persist_action(sys.modules[__name__], package, ledger, envelope)
    else:
        write_json(action_path, envelope)
    job.update({"status": "awaiting_imagegen", "updated_at": iso_now()})
    write_v2_job(root, job)
    print(json.dumps({
        "status": "awaiting_imagegen",
        "action_envelope": action_path.relative_to(root).as_posix(),
        "page_id": page_id,
        "attempt_id": attempt["attempt_id"],
        "attempt_number": attempt_number,
        "attempt_kind": attempt_kind,
        "action_identity": action_identity,
        "expected_asset": envelope["expected_asset"],
        "next_action": (
            "use Codex imagegen once with edit_prompt and referenced_image_path, save the PNG at expected_asset, then provide the matching response"
            if attempt_kind in {"user_authorized_visual_repair", "blocking_repair", "user_authorized_text_revision"} else
            "use Codex imagegen once with the locked prompt, save the PNG at expected_asset, "
            "then provide a matching imagegen response for imagegen-complete"
        ),
        "write_performed": True,
        "execution": ingest_execution_summary(job),
    }, ensure_ascii=False))
    return 0


def command_imagegen_complete(args: argparse.Namespace) -> int:
    root = args.root.resolve()
    kp_path = relative_to_root(args.kp_file, root, "Knowledge Package")
    response_path = relative_to_root(args.response_file, root, "image generation response")
    if not kp_path.is_file() or not response_path.is_file():
        raise CompilerWorkerV2Error("Knowledge Package or image response file is missing")
    package = kp_path.parent
    if (package / "completion_receipt.json").exists():
        raise CompilerWorkerV2Error("completed package cannot consume another image result")
    kp = read_json(kp_path)
    job = validate_job_package_binding(root, kp_path, kp)
    adopted_execution = ensure_ingest_plan(root, job, kp, package)
    source = kp.get("source") if isinstance(kp.get("source"), dict) else {}
    source_identity = str(source.get("source_identity") or "")
    action_path = image_action_envelope_path(package)
    if not action_path.is_file():
        raise CompilerWorkerV2Error("image generation has no authorized action envelope")
    action = read_json(action_path)
    response = read_json(response_path)
    if action.get("schema_version") != V2_IMAGEGEN_ACTION_ENVELOPE_VERSION:
        raise CompilerWorkerV2Error("image action envelope does not match this Knowledge Package")
    if action.get("payload_sha256") != canonical_payload_sha256(action):
        raise CompilerWorkerV2Error("image action envelope hash mismatch")
    if (
        action.get("source_identity") != source_identity
        or action.get("provider") != FORMAL_INFOGRAPHIC_PROVIDER
        or action.get("model") != FORMAL_INFOGRAPHIC_MODEL
    ):
        raise CompilerWorkerV2Error("image action envelope does not match this Knowledge Package")
    if (
        response.get("schema_version") != V2_IMAGEGEN_RESPONSE_VERSION
        or response.get("action_identity") != action.get("action_identity")
        or response.get("attempt_id") != action.get("attempt_id")
        or response.get("provider") != FORMAL_INFOGRAPHIC_PROVIDER
        or response.get("model") != FORMAL_INFOGRAPHIC_MODEL
    ):
        raise CompilerWorkerV2Error("image response does not match the authorized action")
    generation_request = action.get("generation_request")
    if generation_request != FORMAL_INFOGRAPHIC_GENERATION_REQUEST:
        raise CompilerWorkerV2Error(
            "image action lacks the formal Codex imagegen request contract"
        )
    if response.get("generation_request") != generation_request:
        raise CompilerWorkerV2Error(
            "image response does not prove the authorized Codex imagegen request"
        )
    if str(response.get("runtime_tool") or "").strip() != "Codex imagegen":
        raise CompilerWorkerV2Error("image response lacks Codex imagegen runtime evidence")
    infographic = infographic_page(kp, str(action.get("page_id") or ""))
    compile_errors = validate_compiled_infographic_page(kp, package, infographic)
    if compile_errors:
        raise CompilerWorkerV2Error(
            "Knowledge Package changed after infographic compilation: "
            + "; ".join(compile_errors)
        )
    _, current_asset, current_lock_hash = locked_infographic_context(
        package,
        infographic,
    )
    if (
        current_lock_hash != action.get("locked_prompt_sha256")
        or current_asset.relative_to(root).as_posix() != action.get("expected_asset")
    ):
        raise CompilerWorkerV2Error(
            "Knowledge Package changed after image authorization"
        )
    dispatch_evidence = str(response.get("imagegen_dispatch_evidence") or "").strip()
    semantic_evidence = str(response.get("semantic_evidence") or "").strip()
    if len(dispatch_evidence) < 8 or len(semantic_evidence) < 12:
        raise CompilerWorkerV2Error("image response lacks dispatch or semantic readback evidence")
    expected_asset = relative_to_root(
        root / str(action.get("expected_asset") or ""),
        root,
        "authorized infographic asset",
    )
    response_asset = relative_to_root(
        root / str(response.get("asset_path") or ""),
        root,
        "image response asset",
    )
    if response_asset != expected_asset or not expected_asset.is_file():
        raise CompilerWorkerV2Error("image response asset does not match the authorized path")
    png_hash = sha256_bytes(expected_asset.read_bytes())
    if response.get("png_sha256") != png_hash:
        raise CompilerWorkerV2Error("image response PNG hash mismatch")
    if expected_asset.read_bytes()[:8] != b"\x89PNG\r\n\x1a\n":
        raise CompilerWorkerV2Error("formal infographic asset is not a PNG")
    inspection = inspect_image(expected_asset)
    locked_prompt = str(action.get("prompt") or "")
    semantic_readback = response.get("semantic_readback")
    if action.get("attempt_kind") == "user_authorized_text_revision":
        if response.get("imagegen_tool_arguments") != action.get("imagegen_tool_arguments"):
            raise CompilerWorkerV2Error("text revision actual dispatch arguments mismatch")
        reference = relative_to_root(root / action["referenced_image_path"], root, "text revision reference")
        if sha256_bytes(reference.read_bytes()) != action["reference_png_sha256"]:
            raise CompilerWorkerV2Error("text revision reference changed after dispatch")
        if response.get("outcome") == "accepted":
            review = (semantic_readback or {}).get("text_revision_review") or {}
            if review.get("accepted_style_preserved") is not True or review.get("new_text_complete") is not True or not str(review.get("evidence") or "").strip():
                raise CompilerWorkerV2Error("text revision requires actual-image style and new-text review")
    if action.get("attempt_kind") == "blocking_repair":
        validate_blocking_repair_dispatch(root, action, response)
    if action.get("attempt_kind") == "user_authorized_visual_repair":
        if response.get("edit_prompt_sha256") != action.get("edit_prompt_sha256"):
            raise CompilerWorkerV2Error("restricted edit dispatch prompt binding mismatch")
        if response.get("outcome") == "accepted":
            edit_review = (semantic_readback or {}).get("restricted_edit_review", {})
            if (edit_review.get("all_text_preserved") is not True
                    or edit_review.get("only_authorized_icons_removed") is not True
                    or edit_review.get("reference_png_sha256") != action["repair_authorization"]["reference_png_sha256"]):
                raise CompilerWorkerV2Error("restricted edit lacks independent preservation review")
    if response.get("failure_code") == "reverse_review_failed":
        # A failed visual/reader review is recorded as a terminal first attempt;
        # it cannot be mislabeled as a severe text failure to unlock regeneration.
        rejected = semantic_readback if isinstance(semantic_readback, dict) else {}
        errors = image_semantic_readback_errors(rejected, png_sha256=png_hash, prompt=locked_prompt)
        axes = rejected.get("review_axes")
        if (response.get("outcome") != "blocking_failure"
                or rejected.get("formal_ingestion_allowed") is not False
                or not isinstance(axes, dict) or not any(value == "failed" for value in axes.values())
                or not rejected.get("reader_review") or errors):
            raise CompilerWorkerV2Error("rejected visual review lacks bound independent failure evidence")
    if (
        response.get("outcome") == "accepted"
        and prompt_uses_semantic_readback_contract(locked_prompt)
    ):
        readback_errors = image_semantic_readback_errors(
            semantic_readback if isinstance(semantic_readback, dict) else None,
            png_sha256=png_hash,
            prompt=locked_prompt,
        )
        readback_errors.extend(image_reverse_review_errors(semantic_readback))
        if readback_errors:
            raise CompilerWorkerV2Error(
                "image semantic readback failed: " + "; ".join(readback_errors)
            )
    ledger = load_image_attempt_ledger(package, source_identity)
    pages = ledger["pages"]
    assert isinstance(pages, dict)
    page_id = str(action.get("page_id") or "")
    record = pages.get(page_id)
    attempts = record.get("attempts") if isinstance(record, dict) else None
    latest = attempts[-1] if isinstance(attempts, list) and attempts else None
    if not isinstance(record, dict) or not isinstance(latest, dict):
        raise CompilerWorkerV2Error("image response has no matching attempt record")
    binding_errors = footer_variance_binding_errors(semantic_readback, kp, page_id, action.get("action_identity"))
    if binding_errors:
        raise CompilerWorkerV2Error("; ".join(binding_errors))
    if action.get("attempt_kind") == "blocking_repair":
        saved = latest.get("repair_dispatch_contract")
        correction = latest.get("dispatch_correction_authorization")
        if correction is not None:
            errors = dispatch_correction_authorization_errors(kp, page_id, attempts[:-1], correction)
            if errors or action.get("dispatch_correction_authorization") != correction:
                raise CompilerWorkerV2Error("dispatch correction authorization mismatch: " + "; ".join(errors))
        if (len(attempts) != (3 if correction is not None else 2) or not isinstance(saved, dict)
                or any(action.get(key) != value for key, value in saved.items())):
            raise CompilerWorkerV2Error("blocking repair dispatch and attempt ledger disagree")
        errors = blocking_repair_receipt_errors(
            {**latest, "status": response.get("outcome"), "semantic_readback": semantic_readback, "imagegen_tool_arguments": response.get("imagegen_tool_arguments")}, attempts[-2], locked_prompt)
        if errors:
            raise CompilerWorkerV2Error("; ".join(errors))
    if action.get("attempt_kind") == "user_authorized_visual_repair":
        if len(attempts) != 2 or latest.get("attempt_kind") != action["attempt_kind"] or latest.get("repair_authorization") != action.get("repair_authorization"):
            raise CompilerWorkerV2Error("restricted edit authorization and attempt ledger disagree")
        validate_visual_repair_authorization(package, kp, infographic, attempts[0], action.get("repair_authorization"))
    if (
        latest.get("attempt_id") != action.get("attempt_id")
        or latest.get("action_identity") != action.get("action_identity")
    ):
        raise CompilerWorkerV2Error("image response attempt is not the latest authorized action")
    reconsider_text_blocker = (
        latest.get("status") == "blocking_failure"
        and latest.get("failure_code") == "wrong_or_missing_locked_text"
        and response.get("outcome") == "accepted"
    )
    if latest.get("status") in {"accepted", "blocking_failure"} and not reconsider_text_blocker:
        expected_infographic = dict(infographic)
        apply_attempt_result_to_kp(expected_infographic, record, latest)
        if infographic != expected_infographic:
            raise CompilerWorkerV2Error("completed image attempt and Knowledge Package receipt disagree")
        write_performed = adopted_execution
        if action.get("state") != "completed":
            action.update({"state": "completed", "completed_at": iso_now()})
            action["payload_sha256"] = canonical_payload_sha256(action)
            write_json(action_path, action)
            write_performed = True
        expected_job_status = "running" if latest.get("status") == "accepted" else "awaiting_imagegen"
        if job.get("status") != expected_job_status:
            job.update({"status": expected_job_status, "updated_at": iso_now()})
            write_v2_job(root, job)
            write_performed = True
        print(json.dumps({
            "status": "ready" if latest.get("status") == "accepted" else "needs_review",
            "page_id": page_id,
            "attempt_number": latest.get("attempt_number"),
            "outcome": latest.get("status"),
            "write_performed": write_performed,
        }, ensure_ascii=False))
        return 0
    if latest.get("status") != "authorized" and not reconsider_text_blocker:
        raise CompilerWorkerV2Error("image response attempt is not authorized")
    visual = run_infographic_visual_qc(expected_asset)
    requested_outcome = str(response.get("outcome") or "")
    failure_code = str(response.get("failure_code") or "")
    if requested_outcome not in {"accepted", "blocking_failure"}:
        raise CompilerWorkerV2Error("image response outcome is invalid")
    if requested_outcome == "accepted" and visual.get("status") == "ready":
        outcome = "accepted"
        failure_code = ""
    else:
        outcome = "blocking_failure"
        failure_code = failure_code or visual_blocking_failure(visual)
        if failure_code not in IMAGEGEN_BLOCKING_FAILURES:
            raise CompilerWorkerV2Error("blocking image response requires a strict failure code")
    recorded_failure_code = (
        str(latest.get("failure_code") or "")
        if outcome == "accepted" and latest.get("attempt_kind") in {"blocking_repair", "user_authorized_visual_repair"}
        else failure_code
    )
    if reconsider_text_blocker:
        latest.setdefault("prior_rejected_result", {k: latest.get(k) for k in ("status", "failure_code", "completed_at", "png_sha256", "semantic_readback", "semantic_evidence", "footer_text_readback")})
    latest.update({
        "status": outcome,
        "failure_code": recorded_failure_code,
        "completed_at": iso_now(),
        "provider": FORMAL_INFOGRAPHIC_PROVIDER,
        "model": FORMAL_INFOGRAPHIC_MODEL,
        "png_sha256": png_hash,
        "dimensions": {"width": inspection.width, "height": inspection.height},
        "visual_qc": visual,
        "visual_qc_status": visual.get("status"),
        "semantic_evidence": semantic_evidence,
        "dispatch_evidence": dispatch_evidence,
        "page_number_readback": str(response.get("page_number_readback") or ""),
        "footer_text_readback": str(response.get("footer_text_readback") or ""),
        "semantic_readback": (
            semantic_readback if isinstance(semantic_readback, dict) else {}
        ),
    })
    if latest.get("attempt_kind") in {"blocking_repair", "user_authorized_text_revision"}:
        latest["imagegen_tool_arguments"] = response["imagegen_tool_arguments"]
    if latest.get("attempt_kind") == "user_authorized_visual_repair":
        latest["edit_prompt_sha256"] = action["edit_prompt_sha256"]
    if outcome == "accepted" and (
        not latest["page_number_readback"] or not latest["footer_text_readback"]
    ):
        raise CompilerWorkerV2Error("accepted image response lacks page or footer readback")
    if outcome == "accepted":
        expected_visible = visible_text_from_prompt(locked_prompt)
        expected_footer = expected_visible[-1] if expected_visible else ""
        expected_page = expected_visible[-2] if len(expected_visible) > 1 else ""
        expected_footer = accepted_footer_text(semantic_readback, png_hash, locked_prompt)
        if not footer_format_equivalent(expected_footer, latest["footer_text_readback"]):
            raise CompilerWorkerV2Error("accepted footer readback does not match locked prompt")
        if comparable_visible_text(latest["page_number_readback"]) != comparable_visible_text(expected_page):
            raise CompilerWorkerV2Error("accepted page number readback does not match locked prompt")
    write_json(image_attempt_ledger_path(package), ledger)
    apply_attempt_result_to_kp(infographic, record, latest)
    write_json(kp_path, kp)
    action.update({
        "state": "completed",
        "completed_at": iso_now(),
        "outcome": outcome,
        "failure_code": recorded_failure_code,
        "png_sha256": png_hash,
    })
    action["payload_sha256"] = canonical_payload_sha256(action)
    write_json(action_path, action)
    job.update({
        "status": "running" if outcome == "accepted" else "awaiting_imagegen",
        "updated_at": iso_now(),
    })
    if all(page.get("status") == "receipt_verified" for page in kp.get("infographics", [])):
        job["images_ready_at"] = iso_now()
    write_v2_job(root, job)
    print(json.dumps({
        "status": "ready" if outcome == "accepted" else "needs_review",
        "page_id": page_id,
        "attempt_number": latest["attempt_number"],
        "attempt_kind": latest["attempt_kind"],
        "outcome": outcome,
        "failure_code": recorded_failure_code,
        "visual_qc": visual,
        "write_performed": True,
    }, ensure_ascii=False))
    return 0


def image_quality_reconsideration_template(
    root: Path,
    kp_path: Path,
    page_id: str,
) -> dict[str, Any]:
    """Return the one no-generation re-review contract for a historic PNG."""

    kp_path = relative_to_root(kp_path, root, "Knowledge Package")
    if not kp_path.is_file():
        raise CompilerWorkerV2Error("Knowledge Package file is missing")
    package = kp_path.parent
    if (package / "completion_receipt.json").exists():
        raise CompilerWorkerV2Error("completed package cannot reconsider an image review")
    kp = read_json(kp_path)
    job = validate_job_package_binding(root, kp_path, kp)
    if job.get("status") in {"committing", "completed"} or str(job.get("commit_stage") or ""):
        raise CompilerWorkerV2Error(
            "writer has started; historic image review cannot be reconsidered"
        )
    infographic = infographic_page(kp, page_id)
    compile_errors = validate_compiled_infographic_page(kp, package, infographic)
    if compile_errors:
        raise CompilerWorkerV2Error(
            "Knowledge Package changed after infographic compilation: "
            + "; ".join(compile_errors)
        )
    _, asset_path, locked_prompt_sha256 = locked_infographic_context(
        package,
        infographic,
    )
    if not asset_path.is_file() or asset_path.read_bytes()[:8] != b"\x89PNG\r\n\x1a\n":
        raise CompilerWorkerV2Error("historic infographic asset is missing or is not a PNG")
    source = kp.get("source") if isinstance(kp.get("source"), dict) else {}
    source_identity = str(source.get("source_identity") or "")
    ledger = load_image_attempt_ledger(package, source_identity)
    pages = ledger["pages"]
    assert isinstance(pages, dict)
    record = pages.get(page_id)
    attempts = record.get("attempts") if isinstance(record, dict) else None
    latest = attempts[-1] if isinstance(attempts, list) and attempts else None
    if not isinstance(record, dict) or not isinstance(latest, dict):
        raise CompilerWorkerV2Error("historic infographic has no attempt record")
    if record.get("locked_prompt_sha256") != locked_prompt_sha256:
        raise CompilerWorkerV2Error("historic infographic attempt does not bind the locked prompt")
    png_sha256 = sha256_bytes(asset_path.read_bytes())
    if latest.get("png_sha256") != png_sha256:
        raise CompilerWorkerV2Error("historic infographic PNG hash differs from its latest attempt")
    common = {
        "source_identity": source_identity,
        "job_id": job.get("job_id"),
        "page_id": page_id,
        "attempt_id": latest.get("attempt_id"),
        "action_identity": latest.get("action_identity"),
        "asset_path": asset_path.relative_to(root).as_posix(),
        "png_sha256": png_sha256,
        "locked_prompt_sha256": locked_prompt_sha256,
        "knowledge_package": kp_path.relative_to(root).as_posix(),
    }
    prior = latest.get("quality_reconsideration")
    if latest.get("status") == "accepted" and isinstance(prior, dict):
        return {"status": "ready", "already_reconsidered": True, **common}
    if (
        latest.get("status") != "blocking_failure"
        or latest.get("failure_code") != "reverse_review_failed"
    ):
        raise CompilerWorkerV2Error(
            "only a latest reverse-review failure can receive a quality reconsideration"
        )
    prompt_path = package_relative_path(
        package,
        str(infographic.get("locked_prompt_path") or ""),
        "locked infographic prompt",
    )
    prompt = prompt_path.read_text(encoding="utf-8").rstrip("\n")
    historic_errors = quality_only_reverse_review_failure_errors(
        latest.get("semantic_readback")
        if isinstance(latest.get("semantic_readback"), dict)
        else None,
        png_sha256=png_sha256,
        prompt=prompt,
    )
    if historic_errors:
        raise CompilerWorkerV2Error(
            "historic reverse review is not quality-only: " + "; ".join(historic_errors)
        )
    return {
        "status": "needs_review",
        "schema_version": INFOGRAPHIC_QUALITY_RECONSIDERATION_SCHEMA_VERSION,
        "reason": "quality_only_reverse_review_v1",
        **common,
        "semantic_readback": {
            "inspection_method": "independent_image_readback",
            "inspector_identity": "",
            "formal_ingestion_allowed": None,
            "spec_read_receipt": infographic_spec_read_receipt(),
            "review_axes": {
                "source_fidelity": "",
                "visible_text_line_review": "",
                "relationship_integrity": "",
                "footer_signature": "",
            },
            "reader_review": "",
            "inspected_png_sha256": png_sha256,
            "inspected_prompt_sha256": locked_prompt_sha256,
            "visible_text_readback": [],
            "unexpected_visible_text": None,
            "internal_process_text_detected": None,
        },
    }


def command_imagegen_reconsider_quality(args: argparse.Namespace) -> int:
    """Accept one historic PNG only after a fresh, hash-bound core re-review."""

    root = args.root.resolve()
    kp_path = relative_to_root(args.kp_file, root, "Knowledge Package")
    review_path = relative_to_root(args.review_file, root, "quality reconsideration")
    if not review_path.is_file():
        raise CompilerWorkerV2Error("quality reconsideration file is missing")
    review = read_json(review_path)
    page_id = str(args.page_id or "")
    template = image_quality_reconsideration_template(root, kp_path, page_id)
    if not isinstance(review, dict):
        raise CompilerWorkerV2Error("quality reconsideration must be a JSON object")
    expected_fields = (
        "source_identity",
        "job_id",
        "page_id",
        "attempt_id",
        "action_identity",
        "asset_path",
        "png_sha256",
        "locked_prompt_sha256",
    )
    for field in expected_fields:
        if review.get(field) != template.get(field):
            raise CompilerWorkerV2Error("quality reconsideration does not bind " + field)
    if (
        review.get("schema_version") != INFOGRAPHIC_QUALITY_RECONSIDERATION_SCHEMA_VERSION
        or review.get("reason") != "quality_only_reverse_review_v1"
    ):
        raise CompilerWorkerV2Error("quality reconsideration schema or reason is invalid")
    semantic_readback = review.get("semantic_readback")
    package = kp_path.parent
    kp = read_json(kp_path)
    job = validate_job_package_binding(root, kp_path, kp)
    infographic = infographic_page(kp, page_id)
    _, asset_path, _ = locked_infographic_context(package, infographic)
    prompt = package_relative_path(
        package,
        str(infographic.get("locked_prompt_path") or ""),
        "locked infographic prompt",
    ).read_text(encoding="utf-8").rstrip("\n")
    review_errors = image_semantic_readback_errors(
        semantic_readback if isinstance(semantic_readback, dict) else None,
        png_sha256=str(template["png_sha256"]),
        prompt=prompt,
    )
    review_errors.extend(image_reverse_review_errors(
        semantic_readback if isinstance(semantic_readback, dict) else None,
    ))
    if review_errors:
        raise CompilerWorkerV2Error(
            "quality reconsideration did not pass core review: "
            + "; ".join(review_errors)
        )
    source = kp.get("source") if isinstance(kp.get("source"), dict) else {}
    ledger = load_image_attempt_ledger(package, str(source.get("source_identity") or ""))
    record = ledger["pages"].get(page_id)
    attempts = record.get("attempts") if isinstance(record, dict) else None
    latest = attempts[-1] if isinstance(attempts, list) and attempts else None
    if not isinstance(record, dict) or not isinstance(latest, dict):
        raise CompilerWorkerV2Error("historic infographic has no attempt record")
    review_payload_sha256 = canonical_payload_sha256(review)
    if template.get("already_reconsidered"):
        prior = latest.get("quality_reconsideration")
        if (
            not isinstance(prior, dict)
            or prior.get("review_payload_sha256") != review_payload_sha256
        ):
            raise CompilerWorkerV2Error(
                "accepted reconsideration may only replay its original bound review"
            )
        expected_infographic = dict(infographic)
        apply_attempt_result_to_kp(expected_infographic, record, latest)
        if infographic != expected_infographic:
            raise CompilerWorkerV2Error(
                "accepted reconsideration and Knowledge Package receipt disagree"
            )
        print(json.dumps({
            "status": "ready",
            "page_id": page_id,
            "outcome": "accepted",
            "write_performed": False,
        }, ensure_ascii=False))
        return 0
    required_prior_fields = (
        "provider",
        "model",
        "dimensions",
        "visual_qc",
        "visual_qc_status",
        "semantic_evidence",
        "dispatch_evidence",
        "page_number_readback",
        "footer_text_readback",
        "completed_at",
    )
    missing_prior = [field for field in required_prior_fields if not latest.get(field)]
    if missing_prior:
        raise CompilerWorkerV2Error(
            "historic image attempt lacks durable evidence: " + ", ".join(missing_prior)
        )
    if latest.get("visual_qc_status") != "ready":
        raise CompilerWorkerV2Error("historic image attempt did not pass machine visual QC")
    if asset_path.relative_to(root).as_posix() != review.get("asset_path"):
        raise CompilerWorkerV2Error("quality reconsideration asset path changed")
    original = {
        "status": latest.get("status"),
        "failure_code": latest.get("failure_code"),
        "completed_at": latest.get("completed_at"),
        "semantic_readback": latest.get("semantic_readback"),
        "attempt_record_sha256": attempt_record_sha256(record),
    }
    latest["quality_reconsideration"] = {
        "schema_version": INFOGRAPHIC_QUALITY_RECONSIDERATION_SCHEMA_VERSION,
        "reason": "quality_only_reverse_review_v1",
        "reconsidered_at": iso_now(),
        "review_file_sha256": sha256_bytes(review_path.read_bytes()),
        "review_payload_sha256": review_payload_sha256,
        "original": original,
    }
    latest["status"] = "accepted"
    latest["failure_code"] = ""
    latest["semantic_readback"] = semantic_readback
    latest["reconsidered_at"] = latest["quality_reconsideration"]["reconsidered_at"]
    write_json(image_attempt_ledger_path(package), ledger)
    apply_attempt_result_to_kp(infographic, record, latest)
    write_json(kp_path, kp)
    job.update({"status": "running", "updated_at": iso_now()})
    if all(page.get("status") == "receipt_verified" for page in kp.get("infographics", [])):
        job["images_ready_at"] = iso_now()
    write_v2_job(root, job)
    print(json.dumps({
        "status": "ready",
        "page_id": page_id,
        "outcome": "accepted",
        "generation_attempt_count": len(attempts),
        "new_image_generation": False,
        "write_performed": True,
    }, ensure_ascii=False))
    return 0


def image_core_review_confirmation_template(
    root: Path,
    kp_path: Path,
    page_id: str,
) -> dict[str, Any]:
    """Prepare a no-generation current-core review for one accepted PNG."""

    kp_path = relative_to_root(kp_path, root, "Knowledge Package")
    if not kp_path.is_file():
        raise CompilerWorkerV2Error("Knowledge Package file is missing")
    package = kp_path.parent
    if (package / "completion_receipt.json").exists():
        raise CompilerWorkerV2Error("completed package cannot confirm a core image review")
    kp = read_json(kp_path)
    job = validate_job_package_binding(root, kp_path, kp)
    if job.get("status") in {"committing", "completed"} or str(job.get("commit_stage") or ""):
        raise CompilerWorkerV2Error("writer has started; accepted image review cannot be confirmed")
    infographic = infographic_page(kp, page_id)
    compile_errors = validate_compiled_infographic_page(kp, package, infographic)
    if compile_errors:
        raise CompilerWorkerV2Error(
            "Knowledge Package changed after infographic compilation: "
            + "; ".join(compile_errors)
        )
    _, asset_path, locked_prompt_sha256 = locked_infographic_context(package, infographic)
    if not asset_path.is_file() or asset_path.read_bytes()[:8] != b"\x89PNG\r\n\x1a\n":
        raise CompilerWorkerV2Error("accepted infographic asset is missing or is not a PNG")
    source = kp.get("source") if isinstance(kp.get("source"), dict) else {}
    source_identity = str(source.get("source_identity") or "")
    ledger = load_image_attempt_ledger(package, source_identity)
    record = ledger["pages"].get(page_id)
    attempts = record.get("attempts") if isinstance(record, dict) else None
    latest = attempts[-1] if isinstance(attempts, list) and attempts else None
    if not isinstance(record, dict) or not isinstance(latest, dict):
        raise CompilerWorkerV2Error("accepted infographic has no attempt record")
    png_sha256 = sha256_bytes(asset_path.read_bytes())
    if (
        latest.get("status") != "accepted"
        or record.get("locked_prompt_sha256") != locked_prompt_sha256
        or latest.get("png_sha256") != png_sha256
    ):
        raise CompilerWorkerV2Error("accepted infographic receipt does not bind this PNG and prompt")
    common = {
        "source_identity": source_identity,
        "job_id": job.get("job_id"),
        "page_id": page_id,
        "attempt_id": latest.get("attempt_id"),
        "action_identity": latest.get("action_identity"),
        "asset_path": asset_path.relative_to(root).as_posix(),
        "png_sha256": png_sha256,
        "locked_prompt_sha256": locked_prompt_sha256,
        "knowledge_package": kp_path.relative_to(root).as_posix(),
    }
    prompt = package_relative_path(
        package,
        str(infographic.get("locked_prompt_path") or ""),
        "locked infographic prompt",
    ).read_text(encoding="utf-8").rstrip("\n")
    current_errors = image_semantic_readback_errors(
        latest.get("semantic_readback") if isinstance(latest.get("semantic_readback"), dict) else None,
        png_sha256=png_sha256,
        prompt=prompt,
    )
    current_errors.extend(image_reverse_review_errors(
        latest.get("semantic_readback") if isinstance(latest.get("semantic_readback"), dict) else None,
        allow_historical_spec_receipt=True,
    ))
    if not current_errors:
        return {"status": "ready", "already_current": True, **common}
    confirmation = latest.get("core_review_confirmation")
    if isinstance(confirmation, dict):
        confirmed = confirmation.get("semantic_readback")
        confirmation_errors = image_semantic_readback_errors(
            confirmed if isinstance(confirmed, dict) else None,
            png_sha256=png_sha256,
            prompt=prompt,
        )
        confirmation_errors.extend(image_reverse_review_errors(
            confirmed if isinstance(confirmed, dict) else None,
            allow_historical_spec_receipt=True,
        ))
        if not confirmation_errors:
            return {"status": "ready", "already_current": True, **common}
        raise CompilerWorkerV2Error(
            "stored core review confirmation is invalid: " + "; ".join(confirmation_errors)
        )
    return {
        "status": "needs_review",
        "schema_version": INFOGRAPHIC_CORE_REVIEW_CONFIRMATION_SCHEMA_VERSION,
        "reason": "current_core_review_confirmation_v1",
        **common,
        "semantic_readback": {
            "inspection_method": "independent_image_readback",
            "inspector_identity": "",
            "formal_ingestion_allowed": None,
            "spec_read_receipt": infographic_spec_read_receipt(),
            "review_axes": {
                "source_fidelity": "",
                "visible_text_line_review": "",
                "relationship_integrity": "",
                "footer_signature": "",
            },
            "reader_review": "",
            "inspected_png_sha256": png_sha256,
            "inspected_prompt_sha256": locked_prompt_sha256,
            "visible_text_readback": [],
            "unexpected_visible_text": None,
            "internal_process_text_detected": None,
        },
    }


def command_imagegen_confirm_core_review(args: argparse.Namespace) -> int:
    """Save one current core review without producing another PNG."""

    root = args.root.resolve()
    kp_path = relative_to_root(args.kp_file, root, "Knowledge Package")
    review_path = relative_to_root(args.review_file, root, "core review confirmation")
    if not review_path.is_file():
        raise CompilerWorkerV2Error("core review confirmation file is missing")
    review = read_json(review_path)
    page_id = str(args.page_id or "")
    template = image_core_review_confirmation_template(root, kp_path, page_id)
    if not isinstance(review, dict):
        raise CompilerWorkerV2Error("core review confirmation must be a JSON object")
    expected_fields = (
        "source_identity", "job_id", "page_id", "attempt_id", "action_identity",
        "asset_path", "png_sha256", "locked_prompt_sha256",
    )
    for field in expected_fields:
        if review.get(field) != template.get(field):
            raise CompilerWorkerV2Error("core review confirmation does not bind " + field)
    if (
        review.get("schema_version") != INFOGRAPHIC_CORE_REVIEW_CONFIRMATION_SCHEMA_VERSION
        or review.get("reason") != "current_core_review_confirmation_v1"
    ):
        raise CompilerWorkerV2Error("core review confirmation schema or reason is invalid")
    semantic_readback = review.get("semantic_readback")
    package = kp_path.parent
    kp = read_json(kp_path)
    infographic = infographic_page(kp, page_id)
    prompt = package_relative_path(
        package,
        str(infographic.get("locked_prompt_path") or ""),
        "locked infographic prompt",
    ).read_text(encoding="utf-8").rstrip("\n")
    review_errors = image_semantic_readback_errors(
        semantic_readback if isinstance(semantic_readback, dict) else None,
        png_sha256=str(template["png_sha256"]),
        prompt=prompt,
    )
    review_errors.extend(image_reverse_review_errors(
        semantic_readback if isinstance(semantic_readback, dict) else None,
    ))
    if review_errors:
        raise CompilerWorkerV2Error(
            "core review confirmation did not pass: " + "; ".join(review_errors)
        )
    source = kp.get("source") if isinstance(kp.get("source"), dict) else {}
    ledger = load_image_attempt_ledger(package, str(source.get("source_identity") or ""))
    record = ledger["pages"].get(page_id)
    attempts = record.get("attempts") if isinstance(record, dict) else None
    latest = attempts[-1] if isinstance(attempts, list) and attempts else None
    if not isinstance(record, dict) or not isinstance(latest, dict):
        raise CompilerWorkerV2Error("accepted infographic has no attempt record")
    review_payload_sha256 = canonical_payload_sha256(review)
    prior = latest.get("core_review_confirmation")
    if isinstance(prior, dict):
        if prior.get("review_payload_sha256") != review_payload_sha256:
            raise CompilerWorkerV2Error(
                "core review confirmation may only replay its original bound review"
            )
        print(json.dumps({
            "status": "ready", "page_id": page_id, "outcome": "accepted",
            "new_image_generation": False, "write_performed": False,
        }, ensure_ascii=False))
        return 0
    if latest.get("status") != "accepted":
        raise CompilerWorkerV2Error("only the latest accepted attempt can receive a core review confirmation")
    latest["core_review_confirmation"] = {
        "schema_version": INFOGRAPHIC_CORE_REVIEW_CONFIRMATION_SCHEMA_VERSION,
        "reason": "current_core_review_confirmation_v1",
        "confirmed_at": iso_now(),
        "review_file_sha256": sha256_bytes(review_path.read_bytes()),
        "review_payload_sha256": review_payload_sha256,
        "prior_semantic_readback_sha256": canonical_payload_sha256(
            latest.get("semantic_readback") or {}
        ),
        "semantic_readback": semantic_readback,
    }
    latest["semantic_readback"] = semantic_readback
    latest["core_review_confirmed_at"] = latest["core_review_confirmation"]["confirmed_at"]
    write_json(image_attempt_ledger_path(package), ledger)
    apply_attempt_result_to_kp(infographic, record, latest)
    write_json(kp_path, kp)
    print(json.dumps({
        "status": "ready", "page_id": page_id, "outcome": "accepted",
        "new_image_generation": False, "write_performed": True,
    }, ensure_ascii=False))
    return 0


def _unstarted_action_has_runtime_result(package: Path, action_identity: str) -> bool:
    """Refuse migration when a runner left any result for an authorized action."""

    excluded = {
        "knowledge_package.json",
        "imagegen_attempts.json",
        "image_action_envelope.json",
    }
    for candidate in package.glob("*.json"):
        if candidate.name in excluded or candidate.name.startswith("imagegen_unstarted_prompt_version_migration_"):
            continue
        try:
            payload = read_json(candidate)
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict) or payload.get("action_identity") != action_identity:
            continue
        if any(str(payload.get(field) or "").strip() for field in (
            "png_sha256", "imagegen_dispatch_evidence", "dispatch_evidence", "runtime_tool",
        )):
            return True
    return False


def _compiled_page_fields(compiled: dict[str, Any], prompt_path: Path, package: Path) -> dict[str, Any]:
    return {
        "status": "awaiting_imagegen",
        "provider": FORMAL_INFOGRAPHIC_PROVIDER,
        "model": FORMAL_INFOGRAPHIC_MODEL,
        "prompt_compiler": V2_INFOGRAPHIC_PROMPT_COMPILER,
        "prompt_contract_version": compiled["prompt_contract_version"],
        "compiled_plan_schema_version": compiled["schema_version"],
        "compiled_plan_sha256": compiled["compiled_plan_sha256"],
        "template_version": V2_INFOGRAPHIC_TEMPLATE_VERSION,
        "audience": INFOGRAPHIC_STANDALONE_AUDIENCE,
        "content_scope": "source_only",
        "locked_prompt_path": prompt_path.relative_to(package).as_posix(),
        "locked_prompt_sha256": sha256_text(str(compiled["prompt"])),
        "asset_path": compiled["asset_path"],
    }


def _prompt_version_migration_receipt_path(package: Path, index: int) -> Path:
    return package / f"imagegen_unstarted_prompt_version_migration_{index:02d}.json"


def _prompt_version_migration_index(package: Path, target_version: str) -> tuple[int, dict[str, Any] | None]:
    receipts = sorted(package.glob("imagegen_unstarted_prompt_version_migration_*.json"))
    for receipt_path in receipts:
        receipt = read_json(receipt_path)
        if (
            isinstance(receipt, dict)
            and receipt.get("schema_version") == INFOGRAPHIC_UNSTARTED_PROMPT_VERSION_MIGRATION_SCHEMA_VERSION
            and receipt.get("target_prompt_contract_version") == target_version
            and receipt.get("state") == "completed"
        ):
            return int(receipt.get("migration_index") or 0), receipt
    return len(receipts) + 1, None


def command_imagegen_migrate_unstarted_prompt_version(args: argparse.Namespace) -> int:
    """Re-lock only never-generated pages to the current visual prompt contract."""

    root = args.root.resolve()
    kp_path = relative_to_root(args.kp_file, root, "Knowledge Package")
    if not kp_path.is_file():
        raise CompilerWorkerV2Error("Knowledge Package file is missing")
    package = kp_path.parent
    if (package / "completion_receipt.json").exists():
        raise CompilerWorkerV2Error("completed package cannot migrate an unstarted image prompt")
    kp = read_json(kp_path)
    job = validate_job_package_binding(root, kp_path, kp)
    if job.get("status") in {"committing", "completed"} or str(job.get("commit_stage") or ""):
        raise CompilerWorkerV2Error("writer has started; unstarted image prompt cannot migrate")
    target_version = INFOGRAPHIC_PROMPT_CONTRACT_VERSION
    migration_index, completed_receipt = _prompt_version_migration_index(package, target_version)
    if completed_receipt is not None:
        print(json.dumps({
            "status": "ready", "write_performed": False,
            "migration_receipt": _prompt_version_migration_receipt_path(package, migration_index).relative_to(root).as_posix(),
            "migrated_pages": completed_receipt.get("migrated_pages", []),
        }, ensure_ascii=False))
        return 0
    source = kp.get("source") if isinstance(kp.get("source"), dict) else {}
    source_identity = str(source.get("source_identity") or "")
    ledger_path = image_attempt_ledger_path(package)
    if not ledger_path.is_file():
        raise CompilerWorkerV2Error("unstarted image prompt migration requires imagegen_attempts.json")
    ledger = load_image_attempt_ledger(package, source_identity)
    action_path = image_action_envelope_path(package)
    action = read_json(action_path) if action_path.is_file() else {}
    if action and (
        action.get("schema_version") != V2_IMAGEGEN_ACTION_ENVELOPE_VERSION
        or action.get("payload_sha256") != canonical_payload_sha256(action)
    ):
        raise CompilerWorkerV2Error("image action envelope hash mismatch")
    infographics = [item for item in kp.get("infographics") or [] if isinstance(item, dict)]
    candidates = [
        page for page in infographics
        if page.get("status") == "awaiting_imagegen"
        and page.get("prompt_contract_version") != target_version
    ]
    if not candidates:
        raise CompilerWorkerV2Error("no unstarted infographic page requires the current prompt contract")
    if any(
        page.get("status") not in {"awaiting_imagegen", "receipt_verified"}
        for page in infographics
    ):
        raise CompilerWorkerV2Error("unstarted image prompt migration requires only awaiting or verified pages")

    candidate_ids = {str(page.get("page_id") or "") for page in candidates}
    authorized_pages: list[tuple[dict[str, Any], dict[str, Any], dict[str, Any]]] = []
    for page in candidates:
        page_id = str(page.get("page_id") or "")
        _, asset_path, lock_hash = locked_infographic_context(package, page)
        if asset_path.exists():
            raise CompilerWorkerV2Error(f"unstarted image prompt migration found a PNG for {page_id}")
        record = ledger["pages"].get(page_id)
        if record is None:
            continue
        attempts = record.get("attempts") if isinstance(record, dict) else None
        latest = attempts[-1] if isinstance(attempts, list) and attempts else None
        if (
            not isinstance(record, dict)
            or not isinstance(latest, dict)
            or len(attempts) != 1
            or latest.get("status") != "authorized"
            or record.get("locked_prompt_sha256") != lock_hash
            or latest.get("png_sha256")
            or latest.get("completed_at")
            or latest.get("dispatch_evidence")
            or latest.get("imagegen_dispatch_evidence")
            or latest.get("runtime_tool")
        ):
            raise CompilerWorkerV2Error(
                f"unstarted image prompt migration cannot prove {page_id} was never dispatched"
            )
        if _unstarted_action_has_runtime_result(package, str(latest.get("action_identity") or "")):
            raise CompilerWorkerV2Error(
                f"unstarted image prompt migration found a runtime result for {page_id}"
            )
        authorized_pages.append((page, record, latest))
    if len(authorized_pages) > 1:
        raise CompilerWorkerV2Error("only one current authorized image action may be migrated")
    if authorized_pages:
        page, record, latest = authorized_pages[0]
        page_id = str(page.get("page_id") or "")
        if not action or not (
            action.get("state") == "awaiting_imagegen"
            and action.get("page_id") == page_id
            and action.get("attempt_id") == latest.get("attempt_id")
            and action.get("action_identity") == latest.get("action_identity")
            and action.get("locked_prompt_sha256") == record.get("locked_prompt_sha256")
        ):
            raise CompilerWorkerV2Error("authorized image action does not bind the unstarted page")
    elif action:
        raise CompilerWorkerV2Error("image action envelope exists without one migratable unstarted page")

    new_kp = json.loads(json.dumps(kp, ensure_ascii=False))
    new_pages = [item for item in new_kp.get("infographics") or [] if isinstance(item, dict)]
    page_index = {str(page.get("page_id") or ""): index for index, page in enumerate(new_pages, start=1)}
    prior_pages: list[dict[str, Any]] = []
    prompt_updates: list[tuple[Path, bytes, bytes]] = []
    for old_page in candidates:
        page_id = str(old_page.get("page_id") or "")
        new_page = next(page for page in new_pages if str(page.get("page_id") or "") == page_id)
        prior_prompt_path = package_relative_path(
            package, str(old_page.get("locked_prompt_path") or ""), "locked infographic prompt"
        )
        prior_prompt = prior_prompt_path.read_bytes()
        new_page["prompt_contract_version"] = target_version
        compiled = compiled_infographic_page(new_kp, package, new_page)
        if compiled["asset_path"] != old_page.get("asset_path"):
            raise CompilerWorkerV2Error("unstarted prompt migration cannot change an asset path")
        fields = _compiled_page_fields(compiled, prior_prompt_path, package)
        new_page.update(fields)
        new_prompt = (str(compiled["prompt"]) + "\n").encode("utf-8")
        prior_pages.append({
            "page_id": page_id,
            "prompt_contract_version_before": old_page.get("prompt_contract_version"),
            "compiled_plan_sha256_before": old_page.get("compiled_plan_sha256"),
            "locked_prompt_sha256_before": old_page.get("locked_prompt_sha256"),
            "locked_prompt_path": prior_prompt_path.relative_to(package).as_posix(),
            "locked_prompt_sha256_after": fields["locked_prompt_sha256"],
            "compiled_plan_sha256_after": fields["compiled_plan_sha256"],
        })
        prompt_updates.append((prior_prompt_path, prior_prompt, new_prompt))
    for old_page in infographics:
        if str(old_page.get("page_id") or "") in candidate_ids:
            continue
        if validate_compiled_infographic_page(kp, package, old_page):
            raise CompilerWorkerV2Error("verified infographic page is not immutable under prompt migration")

    new_ledger = json.loads(json.dumps(ledger, ensure_ascii=False))
    superseded = new_ledger.setdefault("superseded_unstarted_prompt_versions", [])
    if not isinstance(superseded, list):
        raise CompilerWorkerV2Error("image attempt ledger has invalid superseded migration history")
    for page, record, latest in authorized_pages:
        page_id = str(page.get("page_id") or "")
        superseded.append({
            "migration_index": migration_index,
            "page_id": page_id,
            "reason": "fixed_visual_profile_prompt_version_upgrade_before_generation",
            "record_sha256": canonical_payload_sha256(record),
            "record": record,
        })
        new_ledger["pages"].pop(page_id, None)

    new_job = json.loads(json.dumps(job, ensure_ascii=False))
    lock_ingest_plan(new_job, new_kp, package, contract_reopen=True)
    new_job["execution_contract"]["unstarted_prompt_version_migration"] = {
        "schema_version": INFOGRAPHIC_UNSTARTED_PROMPT_VERSION_MIGRATION_SCHEMA_VERSION,
        "migration_index": migration_index,
        "target_prompt_contract_version": target_version,
        "pages": sorted(candidate_ids),
    }
    new_job.update({"status": "running", "updated_at": iso_now()})

    archive_dir = package / "prompt_version_migrations" / f"migration_{migration_index:02d}"
    archive_dir.mkdir(parents=True, exist_ok=True)
    kp_before = kp_path.read_bytes()
    ledger_before = ledger_path.read_bytes()
    archive_kp = archive_dir / "knowledge_package.before.json"
    archive_ledger = archive_dir / "imagegen_attempts.before.json"
    for destination, payload, label in (
        (archive_kp, kp_before, "Knowledge Package"),
        (archive_ledger, ledger_before, "image attempt ledger"),
    ):
        if destination.exists() and destination.read_bytes() != payload:
            raise CompilerWorkerV2Error(f"unstarted prompt migration {label} archive differs")
        if not destination.exists():
            destination.write_bytes(payload)
        if destination.read_bytes() != payload:
            raise CompilerWorkerV2Error(f"unstarted prompt migration {label} archive readback failed")
    for prompt_path, old_prompt, _ in prompt_updates:
        archived_prompt = archive_dir / "prompts" / prompt_path.name
        archived_prompt.parent.mkdir(parents=True, exist_ok=True)
        if archived_prompt.exists() and archived_prompt.read_bytes() != old_prompt:
            raise CompilerWorkerV2Error("unstarted prompt migration prompt archive differs")
        if not archived_prompt.exists():
            archived_prompt.write_bytes(old_prompt)
        if archived_prompt.read_bytes() != old_prompt:
            raise CompilerWorkerV2Error("unstarted prompt migration prompt archive readback failed")
    archived_action = archive_dir / "image_action_envelope.superseded.json"
    action_before = action_path.read_bytes() if action else b""
    if action:
        if archived_action.exists() and archived_action.read_bytes() != action_before:
            raise CompilerWorkerV2Error("unstarted prompt migration action archive differs")
        if not archived_action.exists():
            action_path.replace(archived_action)
        if not archived_action.is_file() or archived_action.read_bytes() != action_before:
            raise CompilerWorkerV2Error("unstarted prompt migration action archive readback failed")
    for prompt_path, old_prompt, new_prompt in prompt_updates:
        if prompt_path.read_bytes() != old_prompt:
            raise CompilerWorkerV2Error("unstarted prompt migration locked prompt changed during transaction")
        prompt_path.write_bytes(new_prompt)
        if prompt_path.read_bytes() != new_prompt:
            raise CompilerWorkerV2Error("unstarted prompt migration locked prompt write failed")
    write_json(kp_path, new_kp)
    write_json(ledger_path, new_ledger)
    write_v2_job(root, new_job)
    if kp_path.read_bytes() == kp_before:
        raise CompilerWorkerV2Error("unstarted prompt migration did not update its Knowledge Package")
    receipt = {
        "schema_version": INFOGRAPHIC_UNSTARTED_PROMPT_VERSION_MIGRATION_SCHEMA_VERSION,
        "state": "completed",
        "migration_index": migration_index,
        "reason": "fixed_visual_profile_prompt_version_upgrade_before_generation",
        "source_id": source.get("source_id"),
        "source_identity": source_identity,
        "job_id": job.get("job_id"),
        "knowledge_package_path": kp_path.relative_to(root).as_posix(),
        "knowledge_package_sha256_before": sha256_bytes(archive_kp.read_bytes()),
        "knowledge_package_sha256_after": sha256_bytes(kp_path.read_bytes()),
        "attempt_ledger_path": ledger_path.relative_to(root).as_posix(),
        "attempt_ledger_sha256_before": sha256_bytes(archive_ledger.read_bytes()),
        "attempt_ledger_sha256_after": sha256_bytes(ledger_path.read_bytes()),
        "archived_action_envelope_path": archived_action.relative_to(package).as_posix() if action else "",
        "archived_action_envelope_sha256": sha256_bytes(archived_action.read_bytes()) if action else "",
        "target_prompt_contract_version": target_version,
        "migrated_pages": prior_pages,
        "preserved_page_ids": [
            str(page.get("page_id") or "") for page in infographics
            if str(page.get("page_id") or "") not in candidate_ids
        ],
        "completed_at": iso_now(),
    }
    receipt["payload_sha256"] = canonical_payload_sha256(receipt)
    receipt_path = _prompt_version_migration_receipt_path(package, migration_index)
    write_json(receipt_path, receipt)
    print(json.dumps({
        "status": "migrated", "write_performed": True,
        "migration_receipt": receipt_path.relative_to(root).as_posix(),
        "migrated_pages": prior_pages,
        "next_action": "authorize the migrated page with imagegen-authorize before using Codex imagegen",
    }, ensure_ascii=False))
    return 0


def command_imagegen_invalidate(args: argparse.Namespace) -> int:
    root = args.root.resolve()
    kp_path = relative_to_root(args.kp_file, root, "Knowledge Package")
    if not kp_path.is_file():
        raise CompilerWorkerV2Error("Knowledge Package file is missing")
    package = kp_path.parent
    if (package / "completion_receipt.json").exists():
        raise CompilerWorkerV2Error("completed package cannot invalidate an accepted infographic")
    kp = read_json(kp_path)
    job = validate_job_package_binding(root, kp_path, kp)
    source = kp.get("source") if isinstance(kp.get("source"), dict) else {}
    ledger = load_image_attempt_ledger(package, str(source.get("source_identity") or ""))
    pages = ledger["pages"]
    assert isinstance(pages, dict)
    infographic = infographic_page(kp, args.page_id)
    record = pages.get(args.page_id)
    attempts = record.get("attempts") if isinstance(record, dict) else None
    latest = attempts[-1] if isinstance(attempts, list) and attempts else None
    if not isinstance(record, dict) or not isinstance(latest, dict):
        raise CompilerWorkerV2Error("infographic has no accepted attempt to invalidate")
    if len(attempts) >= int(IMAGEGEN_GENERATION_POLICY["max_candidates_per_asset"]):
        raise CompilerWorkerV2Error("infographic page already used its one blocking repair")
    if latest.get("status") != "accepted":
        raise CompilerWorkerV2Error("only the latest accepted attempt can be invalidated")
    try:
        _, accepted_asset_path, accepted_prompt_sha256 = locked_infographic_context(
            package,
            infographic,
        )
    except CompilerWorkerV2Error as exc:
        raise CompilerWorkerV2Error(
            "accepted infographic cannot support invalidation: " + str(exc)
        ) from exc
    # ``locked_infographic_context`` establishes the path inside the package;
    # read it again here so invalidation remains valid even after a newer page
    # has replaced the single current-action envelope.
    accepted_prompt = package_relative_path(
        package,
        str(infographic.get("locked_prompt_path") or ""),
        "accepted infographic prompt",
    )
    prompt = accepted_prompt.read_text(encoding="utf-8").rstrip("\n")
    if not accepted_asset_path.is_file():
        raise CompilerWorkerV2Error("accepted infographic asset cannot support invalidation")
    if (
        record.get("locked_prompt_sha256") != accepted_prompt_sha256
        or latest.get("png_sha256") != sha256_bytes(accepted_asset_path.read_bytes())
    ):
        raise CompilerWorkerV2Error("accepted infographic receipt cannot support invalidation")
    action_path = image_action_envelope_path(package)
    accepted_action = read_json(action_path) if action_path.is_file() else {}
    accepted_action_is_current = (
        accepted_action.get("schema_version") == V2_IMAGEGEN_ACTION_ENVELOPE_VERSION
        and accepted_action.get("payload_sha256")
        == canonical_payload_sha256(accepted_action)
        and accepted_action.get("state") == "completed"
        and accepted_action.get("outcome") == "accepted"
        and accepted_action.get("page_id") == args.page_id
        and accepted_action.get("action_identity") == latest.get("action_identity")
    )
    if accepted_action_is_current and accepted_action.get("prompt") != prompt:
        raise CompilerWorkerV2Error("accepted infographic action prompt cannot support invalidation")
    readback_path = relative_to_root(
        args.semantic_readback,
        root,
        "severe-content semantic readback",
    )
    if not readback_path.is_file():
        raise CompilerWorkerV2Error(
            "image invalidation requires an independent semantic readback file"
        )
    invalidation_readback = read_json(readback_path)
    failure_errors = image_severe_failure_readback_errors(
        invalidation_readback,
        png_sha256=str(latest.get("png_sha256") or ""),
        prompt=prompt,
        failure_code=str(args.failure),
    )
    if failure_errors:
        raise CompilerWorkerV2Error(
            "image invalidation lacks severe content evidence: "
            + "; ".join(failure_errors)
        )
    evidence = str(
        invalidation_readback.get("semantic_evidence") or ""
    ).strip()
    if len(evidence) < 12:
        raise CompilerWorkerV2Error(
            "image invalidation semantic readback lacks concrete evidence"
        )
    latest.update({
        "status": "blocking_failure",
        "failure_code": args.failure,
        "invalidated_at": iso_now(),
        "invalidation_evidence": evidence,
        "invalidation_readback": invalidation_readback,
        "invalidation_readback_sha256": sha256_bytes(readback_path.read_bytes()),
    })
    write_json(image_attempt_ledger_path(package), ledger)
    clear_infographic_receipt(infographic)
    write_json(kp_path, kp)
    invalidated_action = {
        "schema_version": V2_IMAGEGEN_ACTION_ENVELOPE_VERSION,
        "state": "invalidated",
        "job_id": kp.get("job_id"),
        "source_identity": source.get("source_identity"),
        "page_id": args.page_id,
        "failure_code": args.failure,
        "evidence": evidence,
        "invalidated_at": iso_now(),
    }
    invalidated_action.update({
        "semantic_readback_path": readback_path.relative_to(root).as_posix(),
        "semantic_readback_sha256": sha256_bytes(readback_path.read_bytes()),
    })
    invalidated_action["payload_sha256"] = canonical_payload_sha256(invalidated_action)
    preserved_pending_action = bool(
        action_path.is_file()
        and accepted_action.get("state") == "awaiting_imagegen"
        and not accepted_action_is_current
    )
    if accepted_action_is_current or not action_path.is_file():
        write_json(action_path, invalidated_action)
    else:
        existing = sorted(package.glob(f"imagegen_invalidation_{args.page_id}_*.json"))
        invalidation_path = package / (
            f"imagegen_invalidation_{args.page_id}_{len(existing) + 1:02d}.json"
        )
        if invalidation_path.exists():
            raise CompilerWorkerV2Error("image invalidation archive path already exists")
        write_json(invalidation_path, invalidated_action)
    job.update({"status": "awaiting_imagegen", "updated_at": iso_now()})
    job.pop("images_ready_at", None)
    write_v2_job(root, job)
    print(json.dumps({
        "status": "needs_review",
        "page_id": args.page_id,
        "failure_code": args.failure,
        "next_action": (
            "recompile the infographic plan after the pending defective action is archived"
            if preserved_pending_action
            else "authorize one blocking_repair for this page"
        ),
        "pending_action_preserved": preserved_pending_action,
        "write_performed": True,
    }, ensure_ascii=False))
    return 0


def command_project(args: argparse.Namespace) -> int:
    root = args.root.resolve()
    kp_path = relative_to_root(args.kp_file, root, "Knowledge Package")
    if not kp_path.is_file():
        raise CompilerWorkerV2Error("Knowledge Package file is missing")
    package = kp_path.parent
    kp = read_json(kp_path)
    source = kp["source"]
    job = validate_job_package_binding(root, kp_path, kp)
    errors = validate_kp(kp, package)
    if (package / "completion_receipt.json").exists():
        completed = completed_receipt_readback(root, package, kp_path, kp)
        if completed:
            if errors:
                print(json.dumps({
                    "status": "blocked",
                    "stage": "completed_artifact_requires_revision",
                    "already_exists": True,
                    "write_performed": False,
                    "completion_receipt": str(package / "completion_receipt.json"),
                    "production_entry_ready": False,
                    "blocked_reasons": errors,
                }, ensure_ascii=False))
                return 0
            completed_result = {
                "status": "completed",
                "stage": "completed",
                "already_exists": True,
                "write_performed": False,
                "completion_receipt": str(package / "completion_receipt.json"),
                "production_entry_ready": True,
            }
            if completed.get("superseded_by"):
                completed_result["superseded_by"] = completed["superseded_by"]
            print(json.dumps(completed_result, ensure_ascii=False))
            return 0
    now = datetime.now(timezone.utc).isoformat()
    missing_image_receipts = [
        error
        for error in errors
        if (
            "lacks verified gpt-image-2 receipt" in error
            or "imagegen_attempts.json" in error
            or "attempt record" in error
            or "accepted final attempt" in error
        )
    ]
    status = "blocked" if errors else "ready_to_write"
    stage = "awaiting_imagegen" if missing_image_receipts else ("kp_checker" if errors else "kp_verified")
    receipt = {
        "schema_version": SCHEMA_VERSION,
        "job_id": kp.get("job_id"),
        "source_id": source.get("source_id"),
        "source_identity": source.get("source_identity"),
        "status": status,
        "stage": stage,
        "checkpoint": "kp_json",
        "blocked_reasons": errors,
        "package_dir": package.relative_to(root).as_posix(),
        "updated_at": now,
        "write_vault_attempted": False,
    }
    write_json(package / "receipt.json", receipt)
    result = {
        "status": status,
        "stage": stage,
        "blocked_reasons": errors,
        "production_entry_ready": True,
        "write_vault_attempted": False,
    }
    if missing_image_receipts:
        result["next_action"] = (
            "authorize each locked page, generate exactly once with Codex imagegen, "
            "then complete with PNG hash/readback evidence"
        )
    if errors:
        print(json.dumps(result, ensure_ascii=False))
        return 0
    if args.write_vault:
        ensure_ingest_plan(root, job, kp, package)
        ingest_execution_summary(job)
    source_slice = project_source_slice(kp, package)
    result.update({
        "status": "ready_to_write",
        "stage": "projected",
        "source_slice": str(source_slice),
        "source_slice_sha256": sha256_bytes(source_slice.read_bytes()),
    })
    if not args.write_vault:
        print(json.dumps(result, ensure_ascii=False))
        return 0
    if not args.vault:
        raise CompilerWorkerV2Error("--vault is required with --write-vault")
    vault = relative_to_root(args.vault, root, "vault")
    if job.get("status") == "committing" and isinstance(job.get("commit_adapter"), dict):
        adapter_result = write_vault_and_readback(
            root=root,
            vault=vault,
            source_slice=source_slice,
            knowledge_point_count=len(kp.get("knowledge") or []),
            write=False,
        )
    else:
        if job.get("status") != "committing":
            job.update({
                "status": "committing",
                "commit_stage": "adapter_pending",
                "updated_at": iso_now(),
            })
            write_v2_job(root, job)
        try:
            adapter_result = write_vault_and_readback(
                root=root,
                vault=vault,
                source_slice=source_slice,
                knowledge_point_count=len(kp.get("knowledge") or []),
                write=False,
            )
        except CompilerWorkerV2Error:
            adapter_result = write_vault_and_readback(
                root=root,
                vault=vault,
                source_slice=source_slice,
                knowledge_point_count=len(kp.get("knowledge") or []),
                write=True,
            )
    if adapter_result.get("source_identity") != source.get("source_identity"):
        raise CompilerWorkerV2Error("Obsidian readback source identity mismatch")
    concept_graph = project_concept_graph_for_sources(vault, {str(source["source_id"])})
    if concept_graph.get("status") != "ready":
        failed = [
            key for key, value in dict(concept_graph.get("verification") or {}).items()
            if key != "passed" and value is False
        ]
        raise CompilerWorkerV2Error(
            "concept graph projection did not pass readback"
            + (f": {', '.join(failed)}" if failed else "")
        )
    portable_adapter = portable_tool_result(
        adapter_result,
        root=root,
        scalar_path_fields=(
            "registry",
            "knowledge_point_registry",
            "relation_registry",
            "source_note",
            "application_note",
            "asset",
        ),
        list_path_fields=("knowledge_points", "concepts", "assets", "bases"),
    )
    # Preserve the previous checkpoint long enough to distinguish a normal
    # outcome projection from a retry after that projection failed its reader
    # quality audit.  Updating it first would turn reader_quality_pending into
    # outcome_pending and silently reuse the known-bad page.
    retry_reader_quality = (
        job.get("status") == "committing"
        and job.get("commit_stage") == "reader_quality_pending"
    )
    job.update({
        "status": "committing",
        "commit_stage": "outcome_pending",
        "commit_adapter": portable_adapter,
        "updated_at": iso_now(),
    })
    write_v2_job(root, job)
    if isinstance(job.get("commit_outcome"), dict) and not retry_reader_quality:
        outcome = dict(job["commit_outcome"])
    else:
        checkpoint = (
            job.get("outcome_recovery")
            if isinstance(job.get("outcome_recovery"), dict)
            else outcome_recovery_checkpoint(root, vault, str(source["source_id"]))
        )
        # A reader-quality failure may leave a partial page in place.  It is
        # evidence of the failed attempt, not a successful outcome to reuse;
        # regenerate only this source's reader projection after its direct
        # generator/audit correction, without replaying the Vault adapter.
        outcome = None if retry_reader_quality else recover_incremental_outcome(root, checkpoint)
        if outcome is None:
            job.update({
                "status": "committing",
                "commit_stage": "outcome_running",
                "outcome_recovery": checkpoint,
                "updated_at": iso_now(),
            })
            write_v2_job(root, job)
            outcome = generate_incremental_outcome(root, vault, str(source["source_id"]))
    portable_outcome = portable_tool_result(
        outcome,
        root=root,
        scalar_path_fields=("output_dir", "html_index", "obsidian_portal", "current_page"),
        list_path_fields=("index_pages", "pages"),
    )
    job.update({
        "status": "committing",
        "commit_stage": "reader_quality_pending",
        "commit_outcome": portable_outcome,
        "updated_at": iso_now(),
    })
    write_v2_job(root, job)
    revision = kp.get("revision") if isinstance(kp.get("revision"), dict) else {}
    reader_quality = audit_outcome_reader_quality(
        root,
        vault,
        str(source["source_id"]),
        pending_revalidation_revision=revision,
    )
    candidate_path = None
    candidates = None
    if kp.get("semantic_plan_version"):
        try:
            from tools.obsidian_semantic_maintenance import build_new_content_candidates_receipt
        except ModuleNotFoundError:
            from obsidian_semantic_maintenance import build_new_content_candidates_receipt
        candidates = build_new_content_candidates_receipt(
            vault=vault, recent_limit=1, source_ids=[str(source["source_id"])],
            application_paths=[root / portable_adapter["application_note"]],
        )
        candidates["vault"] = vault.relative_to(root).as_posix()
        candidate_path = package / "candidate_receipts" / "new_content_candidates.json"
        write_json(candidate_path, candidates)
    job.update({
        "status": "committing",
        "commit_stage": "completion_pending",
        "commit_reader_quality_audit": reader_quality,
        "updated_at": iso_now(),
    })
    write_v2_job(root, job)
    artifact_paths = [
        portable_adapter.get("source_note"),
        portable_adapter.get("application_note"),
        *(portable_adapter.get("knowledge_points") or []),
        *(portable_adapter.get("assets") or []),
        portable_outcome.get("current_page"),
    ]
    case_manifest: dict[str, Any] | None = None
    if source.get("asset_mode", ASSET_MODE_FORMAL) == ASSET_MODE_CASE:
        try:
            case_manifest = read_case_evidence_manifest(package, source)
        except CaseEvidenceError as exc:
            raise CompilerWorkerV2Error(f"case evidence drifted before completion: {exc}") from exc
        artifact_paths.extend([
            package / str(source["case_evidence_manifest_path"]),
            *case_asset_paths(package, case_manifest),
        ])
    if revision.get("kind") == "case_revalidation":
        for filename in ("revision_origin.json", "revalidation_plan.json"):
            path = package / filename
            if not path.is_file():
                raise CompilerWorkerV2Error(f"case revalidation evidence is missing: {filename}")
            artifact_paths.append(path)
    attempt_ledger_path = image_attempt_ledger_path(package)
    if candidate_path:
        artifact_paths.append(candidate_path)
    if attempt_ledger_path.is_file():
        artifact_paths.append(attempt_ledger_path)
    bundle_path = package / "input_bundle.json"
    if bundle_path.is_file():
        accepted_bundle = read_json(bundle_path)
        if accepted_bundle.get("acquisition_policy") in {"api_first_html_fallback_v1", DIRECT_HTML_ONLY_CAPTURE_POLICY}:
            artifact_paths.extend(package / name for name in ("input_bundle.json", "source_capture_metadata.json", "canonical_source.md", "source_capture.md"))
            artifact_paths.append(package / (accepted_bundle.get("api_capture_path") or accepted_bundle["raw_html_capture_path"]))
        elif accepted_bundle.get("acquisition_policy") == YOUTUBE_CAPTURE_POLICY:
            artifact_paths.extend(package / name for name in (
                "input_bundle.json", "source_capture_metadata.json", "canonical_source.md", "source_capture.md",
                "source_capture_youtube.vtt", "source_capture_youtube.info.json",
            ))
    enrichment = kp.get("application", {})
    for item in enrichment.get("supporting_sources", []):
        artifact_paths.append(package_relative_path(package, item["capture_path"], "supporting source capture"))
    if (package / "application_amendment.json").is_file():
        artifact_paths.append(package / "application_amendment.json")
    manifest = artifact_manifest(artifact_paths, root)
    job_id = str(kp.get("job_id") or "")
    completion_path = package / "completion_receipt.json"
    coverage_audit = build_coverage_audit(kp)
    completed_at = iso_now()
    completion = {
        "schema_version": COMPLETION_SCHEMA_VERSION,
        "status": "completed",
        "job_id": job_id,
        "source_id": source["source_id"],
        "source_input_kind": source["source_input_kind"],
        "source_identity": source["source_identity"],
        "first_accepted_at": job.get("first_accepted_at"),
        "completed_at": completed_at,
        "execution": ingest_execution_summary(job, completed_at=completed_at),
        "knowledge_package_path": kp_path.relative_to(root).as_posix(),
        "knowledge_package_sha256": sha256_bytes(kp_path.read_bytes()),
        "source_slice_path": source_slice.relative_to(root).as_posix(),
        "source_slice_sha256": sha256_bytes(source_slice.read_bytes()),
        "image_attempt_ledger_path": (
            attempt_ledger_path.relative_to(root).as_posix()
            if attempt_ledger_path.is_file()
            else ""
        ),
        "image_attempt_ledger_sha256": (
            sha256_bytes(attempt_ledger_path.read_bytes())
            if attempt_ledger_path.is_file()
            else ""
        ),
        "adapter": portable_adapter,
        "concept_graph": concept_graph,
        "outcome": portable_outcome,
        "reader_quality_audit": reader_quality,
        "coverage_audit": coverage_audit,
        "artifact_manifest": manifest,
    }
    if case_manifest is not None:
        completion["case_evidence"] = {
            "asset_mode": ASSET_MODE_CASE,
            "case_id": case_manifest["case_id"],
            "topic_id": case_manifest["topic_id"],
            "manifest_path": source["case_evidence_manifest_path"],
            "manifest_sha256": source["case_evidence_manifest_sha256"],
            "source_manifest_sha256": source["case_evidence_source_manifest_sha256"],
            "reproduction_entrypoint": case_manifest["reproduction"]["entrypoint"],
            "comparison_screenshot": case_manifest["reproduction"]["comparison_screenshot_path"],
            "case_qualified": True,
            "qualification_basis": "source completion, immutable evidence manifest, local reproduction receipt, writer readback and current reader-quality audit",
        }
    if isinstance(kp.get("revision"), dict):
        completion["revision"] = kp["revision"]
    if candidate_path:
        completion["idea_candidates"] = {"path": candidate_path.relative_to(root).as_posix(),
                                         "status": candidates["status"], "formal_links_created": False}
    completion["payload_sha256"] = canonical_payload_sha256(completion)
    write_json(completion_path, completion)
    if read_json(completion_path).get("payload_sha256") != canonical_payload_sha256(read_json(completion_path)):
        raise CompilerWorkerV2Error("completion receipt readback hash mismatch")
    current_pointer = None
    if source.get("asset_mode", ASSET_MODE_FORMAL) == ASSET_MODE_FORMAL:
        current_pointer = write_current_infographic_pointer(
            root,
            package,
            kp,
            completion_path,
            completion,
        )
    complete_v2_job(root, job_id, completion_path)
    write_json(package / "receipt.json", {
        **receipt,
        "status": "completed",
        "stage": "completed",
        "checkpoint": "completion_receipt",
        "completion_receipt": completion_path.relative_to(root).as_posix(),
        "write_vault_attempted": True,
        "updated_at": iso_now(),
    })
    result.update({
        "status": "completed",
        "stage": "completed",
        "write_vault_attempted": True,
        "completion_receipt": str(completion_path),
        "execution": completion["execution"],
        "coverage_audit": coverage_audit,
        "reader_quality_audit": reader_quality,
        "adapter": adapter_result,
        "concept_graph": concept_graph,
        "outcome": outcome,
    })
    if current_pointer:
        result["infographic_current_pointer"] = current_pointer
    print(json.dumps(result, ensure_ascii=False))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Compiler Worker / Knowledge Package v2 production ingestion tool."
    )
    parser.add_argument("--root", type=Path, default=Path.cwd())
    subparsers = parser.add_subparsers(dest="command", required=True)

    capabilities = subparsers.add_parser("capabilities")
    capabilities.set_defaults(func=command_capabilities)

    accept_job = subparsers.add_parser("accept-job")
    accept_job_source = accept_job.add_mutually_exclusive_group(required=True)
    accept_job_source.add_argument("--url")
    accept_job_source.add_argument("--text-file", type=Path)
    accept_job_source.add_argument("--image-file", type=Path)
    accept_job.add_argument("--source-id", required=True)
    accept_job.add_argument("--job-id")
    accept_job.add_argument("--worker-runtime", choices=("codex_app", "claude_code", "external_worker"), default="codex_app")
    accept_job.set_defaults(func=command_accept_job)

    dispatch_job = subparsers.add_parser("dispatch-job")
    dispatch_job.add_argument("--job-id", required=True)
    dispatch_job.set_defaults(func=command_dispatch_job)

    bind_worker = subparsers.add_parser("bind-worker")
    bind_worker.add_argument("--job-id", required=True)
    bind_worker.add_argument("--worker-instance-id", required=True)
    bind_worker.add_argument("--dispatch-evidence", required=True)
    bind_worker.set_defaults(func=command_bind_worker)

    worker_readback = subparsers.add_parser("worker-readback")
    worker_readback.add_argument("--worker-envelope", type=Path, required=True)
    worker_readback.set_defaults(func=command_worker_readback)

    rebind_existing = subparsers.add_parser("rebind-existing-completion")
    rebind_existing.add_argument("--job-id", required=True)
    rebind_existing.add_argument("--source-id", required=True)
    rebind_existing.add_argument("--vault", type=Path, required=True)
    rebind_existing.add_argument("--source-note", type=Path, required=True)
    rebind_existing.add_argument("--application-note", type=Path, required=True)
    rebind_existing.add_argument("--knowledge-point", type=Path, action="append", required=True)
    rebind_existing.add_argument("--outcome-page", type=Path, required=True)
    rebind_existing.add_argument("--infographic-asset", type=Path, action="append", required=True)
    rebind_existing.add_argument("--html-index", type=Path)
    rebind_existing.add_argument("--obsidian-portal", type=Path)
    rebind_existing.set_defaults(func=command_rebind_existing_completion)

    replace_assets = subparsers.add_parser("replace-completion-assets")
    replace_assets.add_argument("--job-id", required=True)
    replace_assets.add_argument("--source-id", required=True)
    replace_assets.add_argument("--vault", type=Path, required=True)
    replace_assets.add_argument("--previous-completion-receipt", type=Path)
    replace_assets.add_argument("--source-note", type=Path, required=True)
    replace_assets.add_argument("--application-note", type=Path, required=True)
    replace_assets.add_argument("--knowledge-point", type=Path, action="append", required=True)
    replace_assets.add_argument("--outcome-page", type=Path, required=True)
    replace_assets.add_argument("--infographic-asset", type=Path, action="append", required=True)
    replace_assets.add_argument("--html-index", type=Path)
    replace_assets.add_argument("--obsidian-portal", type=Path)
    replace_assets.add_argument("--receipt-suffix", default="fresh_images")
    replace_assets.set_defaults(func=command_replace_completion_assets)

    accept = subparsers.add_parser("accept-url")
    accept.add_argument("--url", required=True)
    accept.add_argument("--source-id", required=True)
    accept.add_argument("--job-id", required=True)
    accept.add_argument("--slug")
    accept.add_argument("--title")
    accept.add_argument("--profile", choices=("article", "paper", "full_body"), default="article")
    accept.add_argument("--case-evidence-file", type=Path)
    accept.add_argument("--capture-mode", choices=(URL_CAPTURE_API_FIRST, URL_CAPTURE_DIRECT_HTML_ONLY), default=URL_CAPTURE_API_FIRST)
    accept.add_argument("--timeout-seconds", type=int, default=30)
    accept.set_defaults(func=command_accept_url)

    accept_text = subparsers.add_parser("accept-text")
    accept_text.add_argument("--text-file", type=Path, required=True)
    accept_text.add_argument("--source-id", required=True)
    accept_text.add_argument("--job-id", required=True)
    accept_text.add_argument("--slug")
    accept_text.add_argument("--title")
    accept_text.add_argument("--profile", choices=("article", "paper", "full_body"), default="article")
    accept_text.set_defaults(func=command_accept_text)

    accept_image = subparsers.add_parser("accept-image")
    accept_image.add_argument("--image-file", type=Path, required=True)
    accept_image.add_argument("--image-analysis-file", type=Path)
    accept_image.add_argument("--source-id", required=True)
    accept_image.add_argument("--job-id", required=True)
    accept_image.add_argument("--slug")
    accept_image.add_argument("--title")
    accept_image.add_argument("--profile", choices=("article", "paper", "full_body"), default="article")
    accept_image.set_defaults(func=command_accept_image)

    prepare_revision = subparsers.add_parser("prepare-infographic-revision")
    prepare_revision.add_argument("--kp-file", type=Path, required=True)
    prepare_revision.add_argument("--plan-file", type=Path, required=True)
    prepare_revision.add_argument("--revision-id")
    prepare_revision.add_argument("--recover-incomplete", action="store_true")
    prepare_revision.set_defaults(func=command_prepare_infographic_revision)

    prepare_case_revalidation = subparsers.add_parser("prepare-case-revalidation")
    prepare_case_revalidation.add_argument("--kp-file", type=Path, required=True)
    prepare_case_revalidation.add_argument("--plan-file", type=Path, required=True)
    prepare_case_revalidation.add_argument("--revision-id")
    prepare_case_revalidation.set_defaults(func=command_prepare_case_revalidation)

    compile_infographics = subparsers.add_parser("compile-infographics")
    compile_infographics.add_argument("--kp-file", type=Path, required=True)
    compile_infographics.add_argument("--plan-repair-file", type=Path)
    compile_infographics.set_defaults(func=command_compile_infographics)

    plan_preflight = subparsers.add_parser("plan-preflight")
    plan_preflight.add_argument("--kp-file", type=Path, required=True)
    plan_preflight.add_argument("--phase", choices=("plan", "compile", "authorize"), default="plan")
    plan_preflight.set_defaults(func=command_plan_preflight)

    plan_text_template = subparsers.add_parser("plan-text-template")
    plan_text_template.add_argument("--kp-file", type=Path, required=True)
    plan_text_template.set_defaults(func=command_plan_text_template)

    imagegen_authorize = subparsers.add_parser("imagegen-authorize")
    imagegen_authorize.add_argument("--kp-file", type=Path, required=True)
    imagegen_authorize.add_argument("--page-id", required=True)
    imagegen_authorize.add_argument(
        "--reason",
        choices=("initial", "blocking_repair", "contract_defect_repair", "user_authorized_visual_repair", "dispatch_correction", "user_authorized_text_revision"),
        default="initial",
    )
    imagegen_authorize.add_argument("--failure", choices=sorted(IMAGEGEN_REPAIRABLE_FAILURES))
    imagegen_authorize.add_argument("--authorization-file", type=Path)
    imagegen_authorize.set_defaults(func=command_imagegen_authorize)

    imagegen_complete = subparsers.add_parser("imagegen-complete")
    imagegen_complete.add_argument("--kp-file", type=Path, required=True)
    imagegen_complete.add_argument("--response-file", type=Path, required=True)
    imagegen_complete.set_defaults(func=command_imagegen_complete)

    imagegen_reconsider_template = subparsers.add_parser("imagegen-reconsider-template")
    imagegen_reconsider_template.add_argument("--kp-file", type=Path, required=True)
    imagegen_reconsider_template.add_argument("--page-id", required=True)
    imagegen_reconsider_template.set_defaults(
        func=lambda args: (
            print(json.dumps(
                image_quality_reconsideration_template(
                    args.root.resolve(),
                    args.kp_file,
                    args.page_id,
                ),
                ensure_ascii=False,
            ))
            or 0
        )
    )

    imagegen_reconsider = subparsers.add_parser("imagegen-reconsider")
    imagegen_reconsider.add_argument("--kp-file", type=Path, required=True)
    imagegen_reconsider.add_argument("--page-id", required=True)
    imagegen_reconsider.add_argument("--review-file", type=Path, required=True)
    imagegen_reconsider.set_defaults(func=command_imagegen_reconsider_quality)

    imagegen_core_review_template = subparsers.add_parser("imagegen-core-review-template")
    imagegen_core_review_template.add_argument("--kp-file", type=Path, required=True)
    imagegen_core_review_template.add_argument("--page-id", required=True)
    imagegen_core_review_template.set_defaults(
        func=lambda args: (
            print(json.dumps(
                image_core_review_confirmation_template(
                    args.root.resolve(), args.kp_file, args.page_id,
                ),
                ensure_ascii=False,
            ))
            or 0
        )
    )

    imagegen_confirm_core_review = subparsers.add_parser("imagegen-confirm-core-review")
    imagegen_confirm_core_review.add_argument("--kp-file", type=Path, required=True)
    imagegen_confirm_core_review.add_argument("--page-id", required=True)
    imagegen_confirm_core_review.add_argument("--review-file", type=Path, required=True)
    imagegen_confirm_core_review.set_defaults(func=command_imagegen_confirm_core_review)

    imagegen_migrate_unstarted = subparsers.add_parser("imagegen-migrate-unstarted")
    imagegen_migrate_unstarted.add_argument("--kp-file", type=Path, required=True)
    imagegen_migrate_unstarted.set_defaults(func=command_imagegen_migrate_unstarted_prompt_version)

    imagegen_invalidate = subparsers.add_parser("imagegen-invalidate")
    imagegen_invalidate.add_argument("--kp-file", type=Path, required=True)
    imagegen_invalidate.add_argument("--page-id", required=True)
    imagegen_invalidate.add_argument(
        "--failure",
        choices=sorted(IMAGEGEN_REPAIRABLE_FAILURES),
        required=True,
    )
    imagegen_invalidate.add_argument(
        "--semantic-readback",
        type=Path,
        required=True,
    )
    imagegen_invalidate.set_defaults(func=command_imagegen_invalidate)

    project = subparsers.add_parser("project")
    project.add_argument("--kp-file", type=Path, required=True)
    project.add_argument("--write-vault", action="store_true")
    project.add_argument("--vault", type=Path)
    project.set_defaults(func=command_project)

    args = parser.parse_args()
    try:
        if args.command in {
            "accept-job",
            "dispatch-job",
            "bind-worker",
            "rebind-existing-completion",
            "replace-completion-assets",
            "accept-url",
            "accept-text",
            "accept-image",
            "prepare-infographic-revision",
            "prepare-case-revalidation",
            "compile-infographics",
            "imagegen-authorize",
            "imagegen-complete",
            "imagegen-reconsider",
            "imagegen-confirm-core-review",
            "imagegen-migrate-unstarted",
            "imagegen-invalidate",
            "project",
        }:
            assert_management_idle(args.root.resolve())
            with compiler_process_lock(args.root.resolve()):
                return args.func(args)
        return args.func(args)
    except InfographicPlanPreflightError as exc:
        print(json.dumps({"status": "blocked", "stage": "plan_preflight", "reason": str(exc), "issues": exc.issues, "write_performed": False, "image_authorization_allowed": False}, ensure_ascii=False), file=sys.stderr)
        return 2
    except (CompilerWorkerV2Error, ValueError, OSError) as exc:
        print(json.dumps({"status": "blocked", "reason": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
