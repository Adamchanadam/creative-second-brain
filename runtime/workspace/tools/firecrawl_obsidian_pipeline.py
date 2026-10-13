"""Compatibility single-source workflow for capture -> slice -> Obsidian adapter.

The tool intentionally keeps the reviewed slice as the formal gate. It can
prepare a capture packet and run the existing adapter checks, but it
does not pretend that image generation or source analysis is fully automatic.

Compatibility boundary: controlled existing three input types and
the formal writer seam still use this pipeline. New compiler schema and
orchestration design route to
docs/Compiler Worker與Knowledge Package設計及驗收規格.md. Results produced here
do not prove that Compiler Worker v2 has reached production cutover.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass, replace
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import urldefrag, urlsplit, urlunsplit

try:
    from tools.source_identity import normalize_url as normalize_capture_url
except ModuleNotFoundError:
    from source_identity import normalize_url as normalize_capture_url

try:
    from tools.validation_manifest import build_embedded_manifest
except ModuleNotFoundError:
    from validation_manifest import build_embedded_manifest  # type: ignore

try:
    from tools.image_source_contract import (
        ImageSourceContractError,
        analysis_from_file,
        canonical_analysis_sha256,
        inspect_image,
        validate_visual_analysis,
    )
except ModuleNotFoundError:
    from image_source_contract import (  # type: ignore
        ImageSourceContractError,
        analysis_from_file,
        canonical_analysis_sha256,
        inspect_image,
        validate_visual_analysis,
    )

try:
    from tools.infographic_prompt_contract import (
        INFOGRAPHIC_PROMPT_CONTRACT_VERSION,
        INFOGRAPHIC_PROMPT_MAX_CHARS,
        INFOGRAPHIC_STANDALONE_AUDIENCE,
        INFOGRAPHIC_TEMPLATE_PROMPT,
        INFOGRAPHIC_TEMPLATE_VERSION,
        INFOGRAPHIC_TEXT_QUALITY_MARKER,
        INFOGRAPHIC_VISIBLE_ITEM_MAX,
        INFOGRAPHIC_VISIBLE_TEXT_MAX_CHARS,
        image_semantic_readback_errors,
        render_gpt_image_2_prompt,
        visible_text_quality_errors,
    )
except ModuleNotFoundError:
    from infographic_prompt_contract import (  # type: ignore
        INFOGRAPHIC_PROMPT_CONTRACT_VERSION,
        INFOGRAPHIC_PROMPT_MAX_CHARS,
        INFOGRAPHIC_STANDALONE_AUDIENCE,
        INFOGRAPHIC_TEMPLATE_PROMPT,
        INFOGRAPHIC_TEMPLATE_VERSION,
        INFOGRAPHIC_TEXT_QUALITY_MARKER,
        INFOGRAPHIC_VISIBLE_ITEM_MAX,
        INFOGRAPHIC_VISIBLE_TEXT_MAX_CHARS,
        image_semantic_readback_errors,
        render_gpt_image_2_prompt,
        visible_text_quality_errors,
    )

try:
    from tools.infographic_compiler import (
        INFOGRAPHIC_CONTENT_USE_GRAPH_TYPES,
        INFOGRAPHIC_GRAPH_TYPE_CONTRACTS,
    )
except ModuleNotFoundError:
    from infographic_compiler import (  # type: ignore
        INFOGRAPHIC_CONTENT_USE_GRAPH_TYPES,
        INFOGRAPHIC_GRAPH_TYPE_CONTRACTS,
    )

try:
    from tools.infographic_attempt_contract import (
        COMPAT_IMAGEGEN_ACTION_ENVELOPE_VERSION as IMAGEGEN_ACTION_ENVELOPE_VERSION,
        COMPAT_IMAGEGEN_RESPONSE_VERSION as IMAGEGEN_RESPONSE_VERSION,
        FORMAL_INFOGRAPHIC_MODEL,
        FORMAL_INFOGRAPHIC_PROVIDER,
        IMAGEGEN_BLOCKING_FAILURES,
        IMAGEGEN_GENERATION_POLICY,
        INFOGRAPHIC_GENERATION_CONTRACT,
    )
except ModuleNotFoundError:
    from infographic_attempt_contract import (  # type: ignore
        COMPAT_IMAGEGEN_ACTION_ENVELOPE_VERSION as IMAGEGEN_ACTION_ENVELOPE_VERSION,
        COMPAT_IMAGEGEN_RESPONSE_VERSION as IMAGEGEN_RESPONSE_VERSION,
        FORMAL_INFOGRAPHIC_MODEL,
        FORMAL_INFOGRAPHIC_PROVIDER,
        IMAGEGEN_BLOCKING_FAILURES,
        IMAGEGEN_GENERATION_POLICY,
        INFOGRAPHIC_GENERATION_CONTRACT,
    )

try:
    from tools.source_ingest_jobs import DEFAULT_JOB_ROOT as SOURCE_INGEST_JOB_ROOT
    from tools.source_ingest_jobs import latest_jobs as source_ingest_latest_jobs
    from tools.source_ingest_jobs import public_job as public_source_ingest_job
except ModuleNotFoundError:
    from source_ingest_jobs import DEFAULT_JOB_ROOT as SOURCE_INGEST_JOB_ROOT  # type: ignore
    from source_ingest_jobs import latest_jobs as source_ingest_latest_jobs  # type: ignore
    from source_ingest_jobs import public_job as public_source_ingest_job  # type: ignore

try:
    from tools.path_budget import (
        PathBudgetError,
        assert_generated_path,
        bounded_slug,
        require_output_source_id,
    )
except ModuleNotFoundError:
    from path_budget import (  # type: ignore
        PathBudgetError,
        assert_generated_path,
        bounded_slug,
        require_output_source_id,
    )


FIRECRAWL_ENDPOINT = "https://api.firecrawl.dev/v2/scrape"
TAVILY_EXTRACT_ENDPOINT = "https://api.tavily.com/extract"
REPO_ROOT = Path(__file__).resolve().parents[1]
ADAPTER = REPO_ROOT / "tools" / "obsidian_vault_adapter.py"
OUTCOME_GENERATOR = REPO_ROOT / "tools" / "outcome_page_generator.py"
INFOGRAPHIC_VISUAL_QC = REPO_ROOT / "tools" / "infographic_visual_qc.py"
DEFAULT_VAULT = Path(os.environ.get("CREATIVE_SECOND_BRAIN_VAULT", "knowledge-base-vault"))
DEFAULT_TIMEOUT_MS = 60000
CAPTURE_PROVIDERS = {"auto", "firecrawl", "tavily", "browser-file"}
BLOCKED_MIN_MAIN_CHARS = 40
REVIEW_MIN_MAIN_CHARS = 500
LONG_FORM_MAIN_CHARS = 12000
LONG_FORM_HEADING_COUNT = 8
MULTI_IMAGE_MARKDOWN_COUNT = 2
INFOGRAPHIC_COVERAGE_TYPES = {"主張", "數字", "限制", "比較", "方法", "例子", "行動"}
INFOGRAPHIC_SOURCE_TONE_TYPES = {"事實", "定義", "建議", "限制", "比較", "例子", "行動", "作者詮釋", "工具定位"}
INFOGRAPHIC_LAYOUT_ARCHETYPES = {"路徑總覽", "對照取捨", "流程步驟", "比較矩陣", "時間線", "概念分區"}
INFOGRAPHIC_READER_CONTEXT_FIELDS = (
    "本圖分工",
    "本頁讀者問題",
    "本頁一句讀後收穫",
    "跨頁承接",
    "圖示語義清單",
    "重複文字 / 數字檢查",
    "正文敘事充足性檢查",
)
IMAGEGEN_REPAIR_MINIMUM_REMAINING_SECONDS = 120


def is_png_asset(path: Path) -> bool:
    try:
        return path.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    except OSError:
        return False
class PipelineStop(Exception):
    def __init__(self, status: str, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


@dataclass(frozen=True)
class UrlCapture:
    url: str
    title: str
    markdown: str
    source_input_kind: str
    source_content_sha256: str | None
    source_bytes: bytes | None
    status_code: int | None
    metadata: dict[str, object]
    provider: str
    capture_method: str
    credential_reference: str
    source_image_sha256: str | None = None
    source_image_mime: str = ""
    source_image_width: int = 0
    source_image_height: int = 0
    source_image_extension: str = ""


@dataclass(frozen=True)
class CaptureQuality:
    status: str
    issues: list[dict[str, str]]
    signals: dict[str, object]
    long_form_segments: list[dict[str, object]]
    infographic_planning: dict[str, object]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Prepare and verify one URL-to-Obsidian workflow."
    )
    parser.add_argument("--root", type=Path, default=Path.cwd(), help="Project root.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    prepare = subparsers.add_parser("prepare", help="Capture one URL, user-provided text, or user-provided image and create a review slice packet.")
    source_input = prepare.add_mutually_exclusive_group(required=True)
    source_input.add_argument("--url")
    source_input.add_argument(
        "--user-text-file",
        type=Path,
        help="Read one user-provided UTF-8 Markdown/text file. Its raw-byte SHA-256 becomes the formal source identity.",
    )
    source_input.add_argument(
        "--user-image-file",
        type=Path,
        help="Read one user-provided PNG, JPEG, or WebP image. Its raw-byte SHA-256 becomes the formal source identity.",
    )
    prepare.add_argument("--source-id", help="Source id such as KS-VS-0007. Defaults to next KS-VS id.")
    prepare.add_argument("--slug", help="Output slug. Defaults to title or URL slug.")
    prepare.add_argument("--title", help="Override source title.")
    prepare.add_argument("--source-type", default="其他")
    prepare.add_argument("--knowledge-use", default="學習材料")
    prepare.add_argument(
        "--source-retention-mode",
        choices=("auto", "summarized_allowed", "full_body_required", "original_image_required"),
        default="auto",
        help="Keep the complete body for text/URL sources; use original_image_required for a user-provided image. summarized_allowed is for existing compatibility repair only.",
    )
    prepare.add_argument("--capture-date", default=str(date.today()))
    prepare.add_argument("--out-root", type=Path, default=Path("outputs"))
    prepare.add_argument(
        "--capture-provider",
        choices=sorted(CAPTURE_PROVIDERS),
        default="auto",
        help="auto tries Firecrawl then Tavily; browser-file reads a local exported text/Markdown file.",
    )
    prepare.add_argument("--firecrawl-response-file", type=Path, help="Use a saved Firecrawl response JSON instead of the network.")
    prepare.add_argument("--tavily-response-file", type=Path, help="Use a saved Tavily extract response JSON instead of the network.")
    prepare.add_argument("--browser-capture-file", type=Path, help="Use text copied/exported from an authenticated browser page.")
    prepare.add_argument("--json", action="store_true")

    image_review = subparsers.add_parser(
        "image-review",
        help="Validate and persist one AI visual-analysis packet for a prepared image source.",
    )
    image_review.add_argument("--source-slice", type=Path, required=True)
    image_review.add_argument("--analysis-file", type=Path, required=True)
    image_review.add_argument("--json", action="store_true")

    prompt = subparsers.add_parser("imagegen-prompt", help="Extract the infographic lock draft for a compatible gpt-image-2 worker.")
    prompt.add_argument("--source-slice", type=Path, required=True)
    prompt.add_argument("--json", action="store_true")

    imagegen_action = subparsers.add_parser(
        "imagegen-action-envelope",
        help="Prepare one locked gpt-image-2 action envelope for a compatible background worker; this command never generates an image.",
    )
    imagegen_action.add_argument("--source-slice", type=Path, required=True)
    imagegen_action.add_argument("--job-id", default="")
    imagegen_action.add_argument("--reason", choices=("initial", "blocking_retry"), default="initial")
    imagegen_action.add_argument("--failure", choices=sorted(IMAGEGEN_BLOCKING_FAILURES))
    imagegen_action.add_argument("--first-accepted-at", help="Legacy UTC timestamp for a blocking repair governed by an old hard-deadline job.")
    imagegen_action.add_argument("--hard-deadline-at", help="Legacy UTC hard deadline; omit both timestamps for the current non-blocking SLA contract.")
    imagegen_action.add_argument("--out", type=Path)
    imagegen_action.add_argument("--json", action="store_true")

    imagegen_complete = subparsers.add_parser(
        "imagegen-complete",
        help="Verify an actual gpt-image-2 result (provider/model/PNG hash) from a compatible background worker and bind it to the locked action.",
    )
    imagegen_complete.add_argument("--source-slice", type=Path, required=True)
    imagegen_complete.add_argument("--response-file", type=Path, required=True)
    imagegen_complete.add_argument("--json", action="store_true")

    attempt = subparsers.add_parser("imagegen-attempt", help="Authorize and persist one bounded image_gen candidate attempt.")
    attempt.add_argument("--source-slice", type=Path, required=True)
    attempt.add_argument("--asset-index", type=int, required=True)
    attempt.add_argument("--reason", choices=("initial", "blocking_retry"), required=True)
    attempt.add_argument("--failure", choices=sorted(IMAGEGEN_BLOCKING_FAILURES))
    attempt.add_argument("--json", action="store_true")

    record = subparsers.add_parser("imagegen-record", help="Consume one authorization and bind it to the generated PNG hash.")
    record.add_argument("--source-slice", type=Path, required=True)
    record.add_argument("--asset-index", type=int, required=True)
    record.add_argument("--attempt-id", required=True)
    record.add_argument("--outcome", choices=("accepted", "blocking_failure"), required=True)
    record.add_argument("--failure", choices=sorted(IMAGEGEN_BLOCKING_FAILURES))
    record.add_argument("--json", action="store_true")

    invalidate = subparsers.add_parser(
        "imagegen-invalidate",
        help="Record a human-detected blocking defect in a previously finalized candidate before vault ingest.",
    )
    invalidate.add_argument("--source-slice", type=Path, required=True)
    invalidate.add_argument("--asset-index", type=int, required=True)
    invalidate.add_argument("--failure", choices=sorted(IMAGEGEN_BLOCKING_FAILURES), required=True)
    invalidate.add_argument("--evidence", required=True)
    invalidate.add_argument("--json", action="store_true")

    finalize = subparsers.add_parser("imagegen-finalize", help="Batch visual QC, semantic result recording, PNG hash binding, and slice status update.")
    finalize.add_argument("--source-slice", type=Path, required=True)
    finalize.add_argument("--review-file", type=Path, required=True)
    finalize.add_argument("--json", action="store_true")

    ingest = subparsers.add_parser("ingest", help="Run a source-local adapter write/readback and incremental outcome update.")
    ingest.add_argument("--source-slice", type=Path, required=True)
    ingest.add_argument("--vault", type=Path, default=DEFAULT_VAULT)
    ingest.add_argument("--max-kp", type=int, default=6)
    ingest.add_argument("--write", action="store_true")
    ingest.add_argument(
        "--full-vault-qc",
        action="store_true",
        help="Opt in to whole-vault health and duplicate scans; never required for one source completion.",
    )
    ingest.add_argument("--source-ingest-job-id", help="Optional durable Terminal/background job id to read back into completion_report.")
    ingest.add_argument("--source-ingest-job-root", type=Path, default=SOURCE_INGEST_JOB_ROOT)
    ingest.add_argument("--completion-report-out", type=Path, help="Optional root-contained JSON file for the full ingest result.")
    ingest.add_argument("--json", action="store_true")

    args = parser.parse_args()
    root = args.root.resolve()

    try:
        if args.command == "prepare":
            return prepare_command(root, args)
        if args.command == "image-review":
            return image_review_command(root, args)
        if args.command == "imagegen-prompt":
            return imagegen_prompt_command(root, args)
        if args.command == "imagegen-action-envelope":
            return imagegen_action_envelope_command(root, args)
        if args.command == "imagegen-complete":
            return imagegen_complete_command(root, args)
        if args.command == "imagegen-attempt":
            return imagegen_attempt_command(root, args)
        if args.command == "imagegen-record":
            return imagegen_record_command(root, args)
        if args.command == "imagegen-invalidate":
            return imagegen_invalidate_command(root, args)
        if args.command == "imagegen-finalize":
            return imagegen_finalize_command(root, args)
        if args.command == "ingest":
            return ingest_command(root, args)
    except PipelineStop as exc:
        payload: dict[str, object] = {
            "status": exc.status,
            "message": exc.message,
            "command": args.command,
            "stage": "pipeline_stop",
        }
        if getattr(args, "command", "") == "ingest":
            payload["source_slice"] = str(getattr(args, "source_slice", ""))
        if getattr(args, "command", "") == "ingest" and getattr(args, "completion_report_out", None):
            try:
                output_path = resolve_inside(root, args.completion_report_out)
                payload["completion_report_out"] = str(output_path.relative_to(root))
                write_completion_report_out(root, args.completion_report_out, payload)
            except Exception as write_exc:  # Avoid recursive handling if durable report writing itself fails.
                payload["completion_report_out_error"] = str(write_exc)
        if getattr(args, "json", False):
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            print(f"{exc.status}: {exc.message}")
        return 1
    raise AssertionError(f"unhandled command: {args.command}")


def effective_source_retention_mode(capture: UrlCapture, requested_mode: str) -> str:
    if capture.source_input_kind == "user_provided_image":
        if requested_mode not in {"auto", "summarized_allowed", "original_image_required"}:
            raise PipelineStop(
                "blocked",
                "user-provided image must use source_retention_mode=original_image_required",
            )
        return "original_image_required"
    if requested_mode == "original_image_required":
        raise PipelineStop(
            "blocked",
            "original_image_required is only valid with --user-image-file",
        )
    return "full_body_required" if requested_mode == "auto" else requested_mode


def capture_file_name(capture: UrlCapture) -> str:
    if capture.source_input_kind == "user_provided_image":
        return f"source_capture{capture.source_image_extension}"
    return "source_capture.md"


def analyze_image_capture_quality(capture: UrlCapture) -> CaptureQuality:
    if not capture.source_image_sha256 or not capture.source_image_mime:
        return CaptureQuality(
            status="blocked",
            issues=[{
                "severity": "blocked",
                "code": "image_inspection_missing",
                "message": "圖片缺少已驗證的 SHA-256 或 MIME；不可建立正式視覺解讀。",
                "recovery": "重新以 --user-image-file 讀取 PNG、JPEG 或 WebP 原圖。",
            }],
            signals={},
            long_form_segments=[],
            infographic_planning={},
        )
    return CaptureQuality(
        status="needs_review",
        issues=[{
            "severity": "needs_review",
            "code": "visual_analysis_required",
            "message": "圖片來源必須完成逐字轉寫、版面結構、錨點、覆蓋表及第二輪校對，才可正式入庫。",
            "recovery": "由 AI Terminal 讀取原圖，建立並提交符合 image-review 契約的 visual analysis JSON。",
        }],
        signals={
            "image_mime_type": capture.source_image_mime,
            "image_width": capture.source_image_width,
            "image_height": capture.source_image_height,
            "image_sha256": capture.source_image_sha256,
        },
        long_form_segments=[],
        infographic_planning={
            "recommended_count": 1,
            "needs_multiple_images": False,
            "reasons": ["原圖是證據資產；是否另做助讀資訊圖須由完整知識覆蓋表決定。"],
        },
    )


def prepare_command(root: Path, args: argparse.Namespace) -> int:
    capture = load_capture(args)
    source_retention_mode = effective_source_retention_mode(capture, args.source_retention_mode)
    source_id = args.source_id or next_source_id(root)
    try:
        source_id = require_output_source_id(source_id)
    except PathBudgetError as exc:
        raise PipelineStop("blocked", str(exc)) from exc
    slug = slugify(args.slug or args.title or capture.title or capture.url or "user_provided_text")
    title = args.title or capture.title or capture.url or "使用者提供文字"
    source_type = (
        "使用者提供圖片"
        if capture.source_input_kind == "user_provided_image" and args.source_type == "其他"
        else args.source_type
    )
    out_root = resolve_inside(root, args.out_root)
    output_dir = out_root / f"vertical_slice_{args.capture_date}_{slug}"
    assets_dir = output_dir / "assets"
    capture_file = output_dir / capture_file_name(capture)
    capture_metadata = output_dir / "source_capture_metadata.json"
    review_packet = output_dir / "review_packet.json"
    source_slice = output_dir / f"{source_id}_{slug}.md"
    try:
        for path, label in (
            (capture_file, "source capture"),
            (capture_metadata, "source capture metadata"),
            (review_packet, "review packet"),
            (source_slice, "source slice"),
        ):
            assert_generated_path(path, label=label)
    except PathBudgetError as exc:
        raise PipelineStop("blocked", str(exc)) from exc
    existing_packet = existing_packet_result(
        root=root,
        output_dir=output_dir,
        capture=capture,
        source_retention_mode=source_retention_mode,
        capture_markdown=capture_file,
        capture_metadata=capture_metadata,
        review_packet=review_packet,
        source_slice=source_slice,
    )
    if existing_packet is not None:
        print_payload(existing_packet, args.json)
        return 0 if existing_packet["status"] == "needs_review" else 1
    if source_retention_mode == "summarized_allowed":
        raise PipelineStop(
            "blocked",
            "new text/URL prepare requires full_body_required; summarized_allowed may only reuse an existing matching historical packet",
        )
    output_dir.mkdir(parents=True, exist_ok=True)
    assets_dir.mkdir(parents=True, exist_ok=True)

    # Website chrome heuristics must never truncate user-supplied prose/code.
    main_markdown = (main_content_markdown(capture.markdown)
                     if capture.source_input_kind == "url" else capture.markdown.strip())
    canonical_body_sha256 = canonical_body_fingerprint(main_markdown) if main_markdown else ""
    representative_anchors = representative_body_anchors(main_markdown)
    capture_content_fingerprint = (
        capture.source_content_sha256
        or capture.source_image_sha256
        or hashlib.sha256(capture.markdown.encode("utf-8")).hexdigest()
    )
    quality = (
        analyze_image_capture_quality(capture)
        if capture.source_input_kind == "user_provided_image"
        else analyze_capture_quality(capture, args.url or "", main_markdown)
    )
    url_binding_status = capture_url_binding_status(capture, quality)
    expected_assets = expected_asset_paths(assets_dir, source_slice.stem, quality)
    try:
        for path in expected_assets:
            assert_generated_path(path, label="expected infographic asset")
    except PathBudgetError as exc:
        raise PipelineStop("blocked", str(exc)) from exc
    expected_asset = expected_assets[0]
    review_gate = capture_review_gate(quality, capture.source_input_kind)

    original_asset_path: Path | None = None
    if capture.source_input_kind == "user_provided_image":
        original_asset_path = assets_dir / f"{source_slice.stem}_original{capture.source_image_extension}"
        try:
            assert_generated_path(original_asset_path, label="original source image")
        except PathBudgetError as exc:
            raise PipelineStop("blocked", str(exc)) from exc
        original_asset_path.write_bytes(capture.source_bytes or b"")
        if hashlib.sha256(original_asset_path.read_bytes()).hexdigest() != capture.source_image_sha256:
            raise PipelineStop("blocked", "prepared image original hash mismatch after staging")
    if capture.source_bytes is not None:
        capture_file.write_bytes(capture.source_bytes)
    else:
        capture_file.write_text(render_capture_markdown(capture), encoding="utf-8")
    capture_metadata.write_text(
        json.dumps(
            {
                "endpoint": capture_endpoint(capture),
                "capture_provider": capture.provider,
                "capture_method": capture.capture_method,
                "source_input_kind": capture.source_input_kind,
                "source_identity": source_identity(capture),
                "source_content_sha256": capture.source_content_sha256,
                "source_image_sha256": capture.source_image_sha256,
                "source_image_mime": capture.source_image_mime,
                "source_image_width": capture.source_image_width,
                "source_image_height": capture.source_image_height,
                "source_capture_file": str(capture_file.relative_to(root)),
                "source_original_asset": (
                    str(original_asset_path.relative_to(root)) if original_asset_path else ""
                ),
                "url": capture.url,
                "source_url_binding_status": url_binding_status,
                "title": title,
                "status_code": capture.status_code,
                "metadata": capture.metadata,
                "markdown_length": len(capture.markdown),
                "capture_content_fingerprint": capture_content_fingerprint,
                "main_markdown_length": len(main_markdown),
                "source_retention_mode": source_retention_mode,
                "infographic_generation_contract": INFOGRAPHIC_GENERATION_CONTRACT,
                "canonical_body_required": source_retention_mode == "full_body_required",
                "canonical_body_sha256": canonical_body_sha256,
                "canonical_body_representative_anchors": representative_anchors,
                "capture_quality": capture_quality_payload(quality),
                "review_gate": review_gate,
                "capture_date": args.capture_date,
                "credential_reference": capture.credential_reference,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    review_packet.write_text(
        json.dumps(
            render_review_packet(
                source_id=source_id,
                title=title,
                capture=capture,
                capture_date=args.capture_date,
                main_markdown=main_markdown,
                source_retention_mode=source_retention_mode,
                canonical_body_sha256=canonical_body_sha256,
                representative_anchors=representative_anchors,
                source_slice=source_slice.relative_to(root),
                expected_asset=expected_asset.relative_to(root),
                expected_assets=[path.relative_to(root) for path in expected_assets],
                quality=quality,
                review_gate=review_gate,
                url_binding_status=url_binding_status,
            ),
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    if quality.status == "blocked":
        result = {
            "status": "blocked",
            "message": "URL capture did not contain enough reliable main content. Do not create a reviewed slice from this packet.",
            "write_blocked_until_reviewed": True,
            "capture_file": str(capture_file.relative_to(root)),
            "capture_markdown": str(capture_file.relative_to(root)),
            "capture_metadata": str(capture_metadata.relative_to(root)),
            "review_packet": str(review_packet.relative_to(root)),
            "expected_asset": str(expected_asset.relative_to(root)),
            "expected_assets": [str(path.relative_to(root)) for path in expected_assets],
            "markdown_length": len(capture.markdown),
            "capture_content_fingerprint": capture_content_fingerprint,
            "main_markdown_length": len(main_markdown),
            "source_retention_mode": source_retention_mode,
            "canonical_body_required": source_retention_mode == "full_body_required",
            "canonical_body_sha256": canonical_body_sha256,
            "canonical_body_representative_anchors": representative_anchors,
            "capture_quality": capture_quality_payload(quality),
            "review_gate": review_gate,
            "validation_manifest": workflow_manifest("url-ingest", root),
        }
        print_payload(result, args.json)
        return 1

    source_slice.write_text(
        render_slice_draft(
            source_id=source_id,
            slug=slug,
            title=title,
            capture=capture,
            capture_date=args.capture_date,
            source_type=source_type,
            knowledge_use=args.knowledge_use,
            main_markdown=main_markdown,
            source_retention_mode=source_retention_mode,
            canonical_body_sha256=canonical_body_sha256,
            representative_anchors=representative_anchors,
            quality=quality,
            url_binding_status=url_binding_status,
        ),
        encoding="utf-8",
    )

    result = {
        "status": "needs_review",
        "message": "Capture packet and draft slice were created, but this is not a reviewed or writable source.",
        "write_blocked_until_reviewed": True,
        "source_slice": str(source_slice.relative_to(root)),
        "capture_file": str(capture_file.relative_to(root)),
        "capture_markdown": str(capture_file.relative_to(root)),
        "capture_metadata": str(capture_metadata.relative_to(root)),
        "review_packet": str(review_packet.relative_to(root)),
        "expected_asset": str(expected_asset.relative_to(root)),
        "expected_assets": [str(path.relative_to(root)) for path in expected_assets],
        "markdown_length": len(capture.markdown),
        "capture_content_fingerprint": capture_content_fingerprint,
        "main_markdown_length": len(main_markdown),
        "source_retention_mode": source_retention_mode,
        "infographic_generation_contract": INFOGRAPHIC_GENERATION_CONTRACT,
        "canonical_body_required": source_retention_mode == "full_body_required",
        "canonical_body_sha256": canonical_body_sha256,
        "canonical_body_representative_anchors": representative_anchors,
        "capture_quality": capture_quality_payload(quality),
        "review_gate": review_gate,
        "next_commands": [
            f"python tools\\firecrawl_obsidian_pipeline.py imagegen-action-envelope --source-slice {source_slice.relative_to(root)} --json",
            f"python tools\\firecrawl_obsidian_pipeline.py ingest --source-slice {source_slice.relative_to(root)} --json",
            f"python tools\\firecrawl_obsidian_pipeline.py ingest --source-slice {source_slice.relative_to(root)} --write --json",
        ],
        "validation_manifest": workflow_manifest("url-ingest", root),
    }
    print_payload(result, args.json)
    return 0


def existing_packet_result(
    *,
    root: Path,
    output_dir: Path,
    capture: UrlCapture,
    source_retention_mode: str,
    capture_markdown: Path,
    capture_metadata: Path,
    review_packet: Path,
    source_slice: Path,
) -> dict[str, object] | None:
    """Prevent a new prepare run from silently overwriting an evidence packet."""
    if not output_dir.exists():
        return None
    if not output_dir.is_dir():
        return {
            "status": "blocked",
            "message": f"prepare output target is not a directory: {output_dir.relative_to(root)}",
            "write_blocked_until_reviewed": True,
        }
    if not any(output_dir.iterdir()):
        return None
    required = (capture_markdown, capture_metadata, review_packet, source_slice)
    if not all(path.is_file() for path in required):
        return {
            "status": "blocked",
            "message": "prepare output directory already contains user files but not one complete reusable evidence packet; refusing overwrite",
            "write_blocked_until_reviewed": True,
        }
    try:
        metadata = json.loads(capture_metadata.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        return {
            "status": "blocked",
            "message": f"existing prepare evidence metadata is unreadable; refusing overwrite: {exc}",
            "write_blocked_until_reviewed": True,
        }
    if not isinstance(metadata, dict) or (
        metadata.get("source_input_kind") != capture.source_input_kind
        or metadata.get("source_identity") != source_identity(capture)
    ):
        return {
            "status": "blocked",
            "message": "prepare output directory belongs to a different source identity; refusing overwrite",
            "write_blocked_until_reviewed": True,
        }
    existing_retention_mode = metadata.get("source_retention_mode", "summarized_allowed")
    if existing_retention_mode != source_retention_mode:
        return {
            "status": "blocked",
            "message": "existing prepare evidence packet uses a different source_retention_mode; refusing to reuse or overwrite evidence",
            "write_blocked_until_reviewed": True,
        }
    if capture.source_input_kind in {"user_provided_text", "user_provided_image"} and capture_markdown.read_bytes() != capture.source_bytes:
        return {
            "status": "blocked",
            "message": "existing user-provided source bytes differ from the requested source; refusing overwrite",
            "write_blocked_until_reviewed": True,
        }
    return {
        "status": "needs_review",
        "message": "matching prepare evidence packet already exists; reused without overwriting evidence",
        "reused_existing_packet": True,
        "write_blocked_until_reviewed": True,
        "source_slice": str(source_slice.relative_to(root)),
        "capture_markdown": str(capture_markdown.relative_to(root)),
        "capture_metadata": str(capture_metadata.relative_to(root)),
        "review_packet": str(review_packet.relative_to(root)),
    }


def capture_url_binding_status(capture: UrlCapture, quality: CaptureQuality) -> str:
    if capture.source_input_kind != "url":
        return "not_applicable"
    if capture.provider != "browser-file":
        return "verified"
    return "verified" if quality.signals.get("browser_source_url_seen") is True else "unverified"


def compile_imagegen_prompts(root: Path, source_slice: Path, text: str, blocks: list[str]) -> tuple[list[Path], list[str], list[dict[str, object]]]:
    source_title = source_title_for_infographic(text)
    primary_work_title = primary_work_title_for_infographic(text)
    primary_work_status = "primary" if primary_work_title else "none"
    footer_title = source_title or first_markdown_heading(text) or source_slice.stem
    source_label = source_label_for_footer(text, footer_title)
    original_date = extract_original_publication_date(text)
    expected_assets = expected_prompt_asset_paths(source_slice, len(blocks))
    prompts: list[str] = []
    issues: list[dict[str, object]] = []
    for index, block in enumerate(blocks, start=1):
        try:
            prompt = render_imagegen_prompt(
                source_title=source_title,
                source_label=source_label,
                original_date=original_date,
                block=block,
                index=index,
                total=len(blocks),
                generated_on=date.today().isoformat(),
                primary_work_title=primary_work_title,
                primary_work_status=primary_work_status,
            )
        except ValueError as exc:
            issues.append({
                "code": "prompt_contract_error",
                "page": index,
                "message": str(exc),
            })
            continue
        prompts.append(prompt)
        if len(prompt) > INFOGRAPHIC_PROMPT_MAX_CHARS:
            issues.append({
                "code": "prompt_too_long",
                "page": index,
                "chars": len(prompt),
                "max": INFOGRAPHIC_PROMPT_MAX_CHARS,
            })
    return expected_assets, prompts, issues


def imagegen_prompt_command(root: Path, args: argparse.Namespace) -> int:
    source_slice = resolve_inside(root, args.source_slice)
    text = source_slice.read_text(encoding="utf-8-sig")
    blocks = extract_infographic_blocks(text)
    _, current_prompts, prompt_issues = compile_imagegen_prompts(root, source_slice, text, blocks)
    if prompt_issues:
        result = {
            "status": "needs_review",
            "message": "資訊圖 prompt contract 未通過；先修文字密度或正式欄位，不可生成。",
            "source_slice": str(source_slice.relative_to(root)),
            "prompt_issues": prompt_issues,
            "generation_allowed": False,
            "validation_manifest": workflow_manifest("infographic", root),
        }
        print_payload(result, args.json)
        return 1
    if not blocks:
        raise PipelineStop("needs_review", "source slice has no infographic lock draft")
    preflight = infographic_preflight(text, blocks)
    if preflight["status"] != "ready":
        result = {
            "status": "needs_review",
            "message": "資訊圖生成前預檢未通過；先修知識覆蓋、頁面分工或文字密度，不可消耗生成次數。",
            "source_slice": str(source_slice.relative_to(root)),
            "preflight": preflight,
            "generation_allowed": False,
            "validation_manifest": workflow_manifest("infographic", root),
        }
        print_payload(result, args.json)
        return 1
    expected_assets, prompts, prompt_length_issues = compile_imagegen_prompts(root, source_slice, text, blocks)
    if prompt_length_issues:
        result = {
            "status": "needs_review",
            "message": "資訊圖 prompt contract 未通過；先修文字密度、拆頁或收斂當頁文字，不可生成。",
            "source_slice": str(source_slice.relative_to(root)),
            "preflight": {**preflight, "prompt_length_issues": prompt_length_issues},
            "generation_allowed": False,
            "validation_manifest": workflow_manifest("infographic", root),
        }
        print_payload(result, args.json)
        return 1
    result = {
        "status": "ready",
        "generation_allowed": True,
        "source_slice": str(source_slice.relative_to(root)),
        "expected_asset": str(expected_assets[0].relative_to(root)),
        "expected_assets": [str(path.relative_to(root)) for path in expected_assets],
        "prompt": prompts[0],
        "prompts": prompts,
        "generation_policy": IMAGEGEN_GENERATION_POLICY,
        "preflight": preflight,
        "prompt_contract": {
            "template_version": INFOGRAPHIC_TEMPLATE_VERSION,
            "prompt_max_chars": INFOGRAPHIC_PROMPT_MAX_CHARS,
            "visible_text_max_chars": INFOGRAPHIC_VISIBLE_TEXT_MAX_CHARS,
            "visible_item_max": INFOGRAPHIC_VISIBLE_ITEM_MAX,
            "internal_workflow_text_allowed": False,
            "audience": INFOGRAPHIC_STANDALONE_AUDIENCE,
            "source_grounded_visible_text_required": True,
            "expected_candidates_per_asset": 1,
        },
        "prompt_lengths": [len(prompt) for prompt in prompts],
        "reference_images": [],
        "attempt_ledger": str(imagegen_attempt_ledger_path(source_slice).relative_to(root)),
        "authorization_command_template": (
            "python tools\\firecrawl_obsidian_pipeline.py imagegen-attempt "
            f"--source-slice {source_slice.relative_to(root)} --asset-index <1-{len(blocks)}> "
            "--reason <initial|blocking_retry> [--failure <blocking_failure>] --json"
        ),
        "batch_review_template": {
            "assets": [
                {
                    "asset_index": index,
                    "attempt_id": "<imagegen-attempt 回傳值>",
                    "semantic_status": "passed|blocking_failure",
                    "semantic_evidence": "<逐字讀回標題、副標、覆蓋內容、原文語氣、承接及跨頁分工的具體證據>",
                    "failure": "",
                }
                for index in range(1, len(blocks) + 1)
            ]
        },
        "finalize_command_template": (
            "python tools\\firecrawl_obsidian_pipeline.py imagegen-finalize "
            f"--source-slice {source_slice.relative_to(root)} --review-file <整批語義覆核.json> --json"
        ),
        "validation_manifest": workflow_manifest("infographic", root),
    }
    print_payload(result, args.json)
    return 0


def write_json_atomically(path: Path, payload: dict[str, object]) -> None:
    temporary = path.with_suffix(path.suffix + f".{uuid.uuid4().hex}.tmp")
    try:
        assert_generated_path(path, label="JSON output")
        assert_generated_path(temporary, label="temporary JSON output")
    except PathBudgetError as exc:
        raise PipelineStop("blocked", str(exc)) from exc
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def action_envelope_path(source_slice: Path) -> Path:
    return source_slice.parent / "imagegen_action_envelope.json"


def imagegen_action_matches_current_prompts(
    existing: dict[str, object],
    root: Path,
    source_slice: Path,
    text: str,
    blocks: list[str],
    expected_assets: list[Path],
    prompts: list[str],
) -> bool:
    existing_assets = existing.get("assets")
    existing_asset_indices = existing.get("asset_indices")
    if not isinstance(existing_assets, list) or not isinstance(existing_asset_indices, list):
        return False
    seen_indices: list[int] = []
    for item in existing_assets:
        if not isinstance(item, dict):
            return False
        index = item.get("asset_index")
        if not isinstance(index, int) or index < 1 or index > len(blocks):
            return False
        seen_indices.append(index)
        prompt = prompts[index - 1]
        expected_asset = expected_assets[index - 1]
        _, lock_hash = imagegen_asset_identity(source_slice, index, text, blocks[index - 1])
        if item.get("lock_hash") != lock_hash:
            return False
        if item.get("prompt_sha256") != hashlib.sha256(prompt.encode("utf-8")).hexdigest():
            return False
        if item.get("expected_asset") != str(expected_asset.relative_to(root)):
            return False
    return seen_indices == existing_asset_indices


def update_current_action_after_finalize(
    root: Path,
    source_slice: Path,
    results: list[dict[str, object]],
) -> None:
    action_path = action_envelope_path(source_slice)
    if not action_path.exists():
        return
    try:
        action = json.loads(action_path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return
    if (
        not isinstance(action, dict)
        or action.get("schema_version") != IMAGEGEN_ACTION_ENVELOPE_VERSION
        or action.get("state") != "awaiting_imagegen"
        or action.get("source_slice") != str(source_slice.relative_to(root))
    ):
        return
    action_assets = action.get("assets")
    if not isinstance(action_assets, list):
        return
    results_by_index = {
        item.get("asset_index"): item
        for item in results
        if isinstance(item, dict)
    }
    if set(results_by_index) != {
        item.get("asset_index")
        for item in action_assets
        if isinstance(item, dict)
    }:
        return
    for item in action_assets:
        if not isinstance(item, dict):
            return
        result = results_by_index.get(item.get("asset_index"))
        if not isinstance(result, dict) or result.get("record", {}).get("attempt", {}).get("attempt_id") != item.get("attempt_id"):
            return
    action["state"] = "completed" if all(item.get("outcome") == "accepted" for item in results) else "blocking_failure"
    action["completed_by"] = "imagegen-finalize"
    write_json_atomically(action_path, action)


def authorize_action_attempts(
    root: Path,
    source_slice: Path,
    *,
    reason: str,
    failure: str | None,
    asset_indexes: list[int],
) -> list[dict[str, object]]:
    """Authorize exactly one candidate per locked page without generating it.

    The actual gpt-image-2 call belongs to a compatible background worker.  This
    helper only reuses the persisted attempt limiter that is already the
    source-of-truth for infographic candidates.
    """
    from contextlib import redirect_stdout
    from io import StringIO

    text = source_slice.read_text(encoding="utf-8-sig")
    blocks = extract_infographic_blocks(text)
    authorized: list[dict[str, object]] = []
    for asset_index in asset_indexes:
        captured = StringIO()
        call_args = argparse.Namespace(
            source_slice=source_slice.relative_to(root),
            asset_index=asset_index,
            reason=reason,
            failure=failure,
            json=True,
        )
        with redirect_stdout(captured):
            status = imagegen_attempt_command(root, call_args)
        try:
            payload = json.loads(captured.getvalue())
        except json.JSONDecodeError as exc:  # pragma: no cover - defensive for a legacy helper.
            raise PipelineStop("needs_review", "imagegen attempt authorization returned invalid JSON") from exc
        if status != 0 or payload.get("status") != "authorized":
            raise PipelineStop("needs_review", str(payload.get("reason") or payload.get("message") or "imagegen candidate was not authorized"))
        authorized.append(payload)
    return authorized


def action_asset_indexes(source_slice: Path, *, reason: str, failure: str | None) -> list[int]:
    """Select the only pages that may be generated, before any authorization writes."""
    text = source_slice.read_text(encoding="utf-8-sig")
    blocks = extract_infographic_blocks(text)
    ledger = load_imagegen_attempt_ledger(source_slice)
    assets = ledger["assets"]
    assert isinstance(assets, dict)
    if reason == "initial":
        indexes: list[int] = []
        for index, block in enumerate(blocks, start=1):
            asset_id, lock_hash = imagegen_asset_identity(source_slice, index, text, block)
            record = assets.get(asset_id)
            attempts = record.get("attempts") if isinstance(record, dict) and record.get("lock_hash") == lock_hash else []
            if attempts:
                raise PipelineStop("needs_review", "initial_generation cannot regenerate an already attempted infographic page")
            indexes.append(index)
        return indexes
    if failure not in IMAGEGEN_BLOCKING_FAILURES:
        raise PipelineStop("needs_review", "blocking_repair requires one strict blocking failure code")
    indexes = []
    for index, block in enumerate(blocks, start=1):
        asset_id, lock_hash = imagegen_asset_identity(source_slice, index, text, block)
        record = assets.get(asset_id)
        attempts = record.get("attempts") if isinstance(record, dict) and record.get("lock_hash") == lock_hash else None
        latest = attempts[-1] if isinstance(attempts, list) and attempts else None
        if isinstance(latest, dict) and latest.get("status") == "accepted":
            continue
        if isinstance(latest, dict) and latest.get("status") == "blocking_failure":
            if latest.get("failure") != failure:
                continue
            if len(attempts) == 1:
                indexes.append(index)
                continue
            raise PipelineStop("needs_review", f"asset {index} already used its one blocking repair")
        if isinstance(latest, dict):
            raise PipelineStop("needs_review", f"asset {index} is not eligible for the requested blocking repair")
    if not indexes:
        raise PipelineStop("needs_review", "blocking_repair has no failed infographic page; accepted pages are locked and cannot regenerate")
    return indexes


def parse_utc_timestamp(value: str, label: str) -> datetime:
    try:
        normalized = value.replace("Z", "+00:00")
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise PipelineStop("needs_review", f"{label} must be a UTC ISO timestamp") from exc
    if parsed.tzinfo is None:
        raise PipelineStop("needs_review", f"{label} must include timezone information")
    return parsed.astimezone(timezone.utc)


def imagegen_action_envelope_command(root: Path, args: argparse.Namespace) -> int:
    source_slice = resolve_inside(root, args.source_slice)
    text = source_slice.read_text(encoding="utf-8-sig")
    blocks = extract_infographic_blocks(text)
    preflight = infographic_preflight(text, blocks)
    if preflight["status"] != "ready":
        raise PipelineStop("needs_review", "locked infographic brief is not ready for gpt-image-2")
    expected_assets, prompts, prompt_length_issues = compile_imagegen_prompts(root, source_slice, text, blocks)
    if prompt_length_issues:
        raise PipelineStop("needs_review", "locked infographic prompt exceeds the safe generation contract")
    existing_path = action_envelope_path(source_slice)
    effective_job_id = str(args.job_id or "")
    if existing_path.exists():
        try:
            existing = json.loads(existing_path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError) as exc:
            raise PipelineStop("needs_review", f"existing imagegen action envelope is unreadable: {exc}") from exc
        if not isinstance(existing, dict):
            raise PipelineStop("needs_review", "existing imagegen action envelope must be an object")
        if effective_job_id and str(existing.get("job_id") or "") != effective_job_id:
            raise PipelineStop("needs_review", "imagegen action belongs to a different job; preserve its original job binding")
        effective_job_id = str(existing.get("job_id") or "")
        if (
            existing.get("schema_version") == IMAGEGEN_ACTION_ENVELOPE_VERSION
            and existing.get("source_slice") == str(source_slice.relative_to(root))
            and existing.get("provider") == FORMAL_INFOGRAPHIC_PROVIDER
            and existing.get("model") == FORMAL_INFOGRAPHIC_MODEL
            and existing.get("state") == "awaiting_imagegen"
            and existing.get("reason") == args.reason
            and existing.get("failure", "") == (args.failure or "")
            and imagegen_action_matches_current_prompts(existing, root, source_slice, text, blocks, expected_assets, prompts)
        ):
            result = {**existing, "status": "awaiting_imagegen", "write_performed": False}
            print_payload(result, args.json)
            return 0
    if args.reason == "blocking_retry":
        if bool(args.first_accepted_at) != bool(args.hard_deadline_at):
            raise PipelineStop("needs_review", "blocking_repair must provide both legacy hard-deadline timestamps or neither")
        if args.first_accepted_at and args.hard_deadline_at:
            hard_deadline = parse_utc_timestamp(args.hard_deadline_at, "hard_deadline_at")
            remaining = (hard_deadline - datetime.now(timezone.utc)).total_seconds()
            if remaining < IMAGEGEN_REPAIR_MINIMUM_REMAINING_SECONDS:
                raise PipelineStop("blocked", "blocking_repair is not authorized because less than 120 seconds remains before the legacy hard deadline")
            parse_utc_timestamp(args.first_accepted_at, "first_accepted_at")
    asset_indexes = action_asset_indexes(source_slice, reason=args.reason, failure=args.failure)
    authorized = authorize_action_attempts(root, source_slice, reason=args.reason, failure=args.failure, asset_indexes=asset_indexes)
    action_path = resolve_inside(root, args.out) if args.out else existing_path
    assets: list[dict[str, object]] = []
    for index, authorization in zip(asset_indexes, authorized):
        prompt = prompts[index - 1]
        expected_asset = expected_assets[index - 1]
        block = blocks[index - 1]
        _, lock_hash = imagegen_asset_identity(source_slice, index, text, block)
        assets.append({
            "asset_index": index,
            "attempt_id": authorization["attempt"]["attempt_id"],
            "attempt_number": authorization["attempt"]["attempt_number"],
            "lock_hash": lock_hash,
            "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
            "expected_asset": str(expected_asset.relative_to(root)),
            "prompt": prompt,
        })
    envelope: dict[str, object] = {
        "schema_version": IMAGEGEN_ACTION_ENVELOPE_VERSION,
        "action_id": f"IMGACT-{uuid.uuid4().hex}",
        "state": "awaiting_imagegen",
        "requested_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "job_id": effective_job_id,
        "source_slice": str(source_slice.relative_to(root)),
        "provider": FORMAL_INFOGRAPHIC_PROVIDER,
        "model": FORMAL_INFOGRAPHIC_MODEL,
        "attempt_kind": "initial_generation" if args.reason == "initial" else "blocking_repair",
        "reason": args.reason,
        "failure": args.failure or "",
        "asset_indices": asset_indexes,
        "infographic_generation_contract": INFOGRAPHIC_GENERATION_CONTRACT,
        "assets": assets,
        "response_contract": {
            "schema_version": IMAGEGEN_RESPONSE_VERSION,
            "provider": FORMAL_INFOGRAPHIC_PROVIDER,
            "model": FORMAL_INFOGRAPHIC_MODEL,
            "asset_path": "must equal expected_asset and point to the actual gpt-image-2 PNG inside the project root",
            "image_sha256": "must equal the read-back SHA-256 of asset_path",
            "imagegen_dispatch_evidence": "opaque compatible-worker gpt-image-2 dispatch/readback evidence; the repo records it but does not claim to independently prove a tool call",
            "imagegen_wait_seconds": "actual gpt-image-2/network wait only; non-negative",
            "imagegen_wait_evidence": "required when wait is greater than zero",
        },
        "background_worker_instruction": "Use a configured gpt-image-2 provider adapter with each locked prompt. Do not use HTML/SVG/CSS, placeholders, fixtures, old PNGs, or a local renderer. Save each actual gpt-image-2 result to expected_asset, then write the response JSON and run imagegen-complete.",
    }
    write_json_atomically(action_path, envelope)
    result = {
        "status": "awaiting_imagegen",
        "action_envelope": str(action_path.relative_to(root)),
        "job_id": envelope["job_id"],
        "provider": FORMAL_INFOGRAPHIC_PROVIDER,
        "model": FORMAL_INFOGRAPHIC_MODEL,
        "assets": [
            {
                key: item[key]
                for key in (
                    "asset_index",
                    "attempt_id",
                    "expected_asset",
                    "lock_hash",
                    "prompt_sha256",
                )
            }
            for item in assets
        ],
        "write_performed": True,
    }
    print_payload(result, args.json)
    return 0


def imagegen_complete_command(root: Path, args: argparse.Namespace) -> int:
    source_slice = resolve_inside(root, args.source_slice)
    response_path = resolve_inside(root, args.response_file)
    try:
        response = json.loads(response_path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PipelineStop("needs_review", f"gpt-image-2 response file is unreadable: {exc}") from exc
    if not isinstance(response, dict) or response.get("schema_version") != IMAGEGEN_RESPONSE_VERSION:
        raise PipelineStop("needs_review", "gpt-image-2 response has an invalid schema version")
    action_ref = response.get("action_envelope")
    if not isinstance(action_ref, str) or not action_ref:
        raise PipelineStop("needs_review", "gpt-image-2 response does not identify its action envelope")
    action_path = resolve_inside(root, Path(action_ref))
    try:
        action = json.loads(action_path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PipelineStop("needs_review", f"gpt-image-2 action envelope is unreadable: {exc}") from exc
    already_completed = isinstance(action, dict) and action.get("state") == "completed"
    if (
        not isinstance(action, dict)
        or action.get("schema_version") != IMAGEGEN_ACTION_ENVELOPE_VERSION
        or action.get("state") not in {"awaiting_imagegen", "completed"}
        or action.get("source_slice") != str(source_slice.relative_to(root))
        or action.get("provider") != FORMAL_INFOGRAPHIC_PROVIDER
        or action.get("model") != FORMAL_INFOGRAPHIC_MODEL
    ):
        raise PipelineStop("needs_review", "gpt-image-2 action envelope does not match this formal source")
    if response.get("action_id") != action.get("action_id"):
        raise PipelineStop("needs_review", "gpt-image-2 response action id does not match the locked action")
    if response.get("provider") != FORMAL_INFOGRAPHIC_PROVIDER or response.get("model") != FORMAL_INFOGRAPHIC_MODEL:
        raise PipelineStop(
            "needs_review",
            f"formal infographic must report provider={FORMAL_INFOGRAPHIC_PROVIDER} "
            f"and model={FORMAL_INFOGRAPHIC_MODEL}",
        )
    dispatch_evidence = str(response.get("imagegen_dispatch_evidence") or response.get("codex_dispatch_evidence") or "").strip()
    if len(dispatch_evidence) < 8:
        raise PipelineStop("needs_review", "gpt-image-2 response requires background-worker dispatch/readback evidence")
    wait_seconds = response.get("imagegen_wait_seconds", 0.0)
    if not isinstance(wait_seconds, (int, float)) or float(wait_seconds) < 0:
        raise PipelineStop("needs_review", "gpt-image-2 response imagegen_wait_seconds must be non-negative")
    wait_evidence = str(response.get("imagegen_wait_evidence") or "").strip()
    if wait_seconds and len(wait_evidence) < 8:
        raise PipelineStop("needs_review", "gpt-image-2 response needs durable wait evidence when it claims external waiting")
    response_assets = response.get("assets")
    action_assets = action.get("assets")
    if not isinstance(response_assets, list) or not isinstance(action_assets, list) or len(response_assets) != len(action_assets):
        raise PipelineStop("needs_review", "gpt-image-2 response assets do not match the locked action")
    by_index = {item.get("asset_index"): item for item in response_assets if isinstance(item, dict)}
    if len(by_index) != len(response_assets):
        raise PipelineStop("needs_review", "gpt-image-2 response repeats or omits an asset index")
    text = source_slice.read_text(encoding="utf-8-sig")
    blocks = extract_infographic_blocks(text)
    _, current_prompts, prompt_issues = compile_imagegen_prompts(root, source_slice, text, blocks)
    if prompt_issues:
        raise PipelineStop("needs_review", "current infographic prompt exceeds the formal prompt contract")
    prepared: list[dict[str, object]] = []
    for action_asset in action_assets:
        if not isinstance(action_asset, dict):
            raise PipelineStop("needs_review", "gpt-image-2 action has an invalid asset")
        index = action_asset.get("asset_index")
        item = by_index.get(index)
        if not isinstance(index, int) or not isinstance(item, dict):
            raise PipelineStop("needs_review", "gpt-image-2 response has no result for a locked asset")
        if item.get("attempt_id") != action_asset.get("attempt_id"):
            raise PipelineStop("needs_review", f"asset {index} attempt id does not match the locked action")
        if item.get("provider") != FORMAL_INFOGRAPHIC_PROVIDER or item.get("model") != FORMAL_INFOGRAPHIC_MODEL:
            raise PipelineStop("needs_review", f"asset {index} was not generated by gpt-image-2")
        asset_path = resolve_inside(root, Path(str(item.get("asset_path") or "")))
        expected_asset = resolve_inside(root, Path(str(action_asset.get("expected_asset") or "")))
        if asset_path != expected_asset:
            raise PipelineStop("needs_review", f"asset {index} path is not the locked gpt-image-2 destination")
        if not asset_path.exists() or not is_png_asset(asset_path):
            raise PipelineStop("needs_review", f"asset {index} is missing or not a PNG")
        image_sha256 = hashlib.sha256(asset_path.read_bytes()).hexdigest()
        if item.get("image_sha256") != image_sha256:
            raise PipelineStop("needs_review", f"asset {index} PNG hash does not match its gpt-image-2 response")
        _, expected_lock_hash = imagegen_asset_identity(source_slice, index, text, blocks[index - 1])
        action_prompt = str(action_asset.get("prompt") or "")
        prompt_sha256 = str(action_asset.get("prompt_sha256") or "")
        if prompt_sha256:
            current_prompt_sha256 = hashlib.sha256(current_prompts[index - 1].encode("utf-8")).hexdigest()
            if (
                hashlib.sha256(action_prompt.encode("utf-8")).hexdigest() != prompt_sha256
                or current_prompt_sha256 != prompt_sha256
            ):
                raise PipelineStop("needs_review", f"asset {index} no longer matches the locked infographic prompt")
        elif action_asset.get("lock_hash") != expected_lock_hash:
            raise PipelineStop("needs_review", f"asset {index} no longer matches the locked infographic brief")
        semantic_readback = item.get("semantic_readback")
        visual = run_infographic_visual_qc(asset_path)
        if (
            visual.get("status") == "ready"
            and INFOGRAPHIC_PROMPT_CONTRACT_VERSION in action_prompt
        ):
            readback_errors = image_semantic_readback_errors(
                semantic_readback if isinstance(semantic_readback, dict) else None,
                png_sha256=image_sha256,
                prompt=action_prompt,
            )
            if readback_errors:
                raise PipelineStop(
                    "needs_review",
                    f"asset {index} image semantic readback failed: {'; '.join(readback_errors)}",
                )
        prepared.append({
            "asset_index": index,
            "attempt_id": item["attempt_id"],
            "visual": visual,
            "image_sha256": image_sha256,
            "prompt_sha256": prompt_sha256,
            "semantic_readback": semantic_readback,
        })
    if already_completed:
        # A worker can stop after imagegen-complete commits the source slice,
        # attempt ledger and action state but before the job runner records its
        # timing/checkpoint. Replaying the identical response must validate the
        # current PNG/readback without consuming the authorization a second time.
        if any(not isinstance(item.get("visual"), dict) or item["visual"].get("status") != "ready" for item in prepared):
            raise PipelineStop("needs_review", "completed gpt-image-2 action no longer passes mechanical PNG validation")
        try:
            verified = validate_imagegen_attempts_for_ingest(root, source_slice)
        except PipelineStop as exc:
            raise PipelineStop("needs_review", f"completed gpt-image-2 action cannot be replayed: {exc.message}") from exc
        result = {
            "status": "ready",
            "provider": FORMAL_INFOGRAPHIC_PROVIDER,
            "model": FORMAL_INFOGRAPHIC_MODEL,
            "action_envelope": str(action_path.relative_to(root)),
            "accepted_count": len(prepared),
            "blocking_count": 0,
            "results": [
                {
                    "asset_index": item["asset_index"],
                    "outcome": "accepted",
                    "failure": "",
                    "visual_qc": item["visual"],
                    "record": {"replayed_completed_action": True},
                }
                for item in prepared
            ],
            "imagegen_wait_seconds": float(wait_seconds),
            "imagegen_wait_evidence": wait_evidence,
            "replayed_completed_action": True,
            "verified_assets": verified.get("assets", []),
            "next_action": "ingest",
        }
        print_payload(result, args.json)
        return 0
    results: list[dict[str, object]] = []
    updated_blocks = list(blocks)
    for item in prepared:
        index = int(item["asset_index"])
        visual = item["visual"]
        assert isinstance(visual, dict)
        outcome = "accepted" if visual.get("status") == "ready" else "blocking_failure"
        failure = "" if outcome == "accepted" else visual_failure_code(visual)
        record = consume_imagegen_attempt(
            root,
            source_slice,
            asset_index=index,
            attempt_id=str(item["attempt_id"]),
            outcome=outcome,
            failure=failure or None,
            finalized_by="imagegen-complete",
            visual_qc=visual,
            provider=FORMAL_INFOGRAPHIC_PROVIDER,
            model=FORMAL_INFOGRAPHIC_MODEL,
            action_id=str(action.get("action_id")),
            imagegen_dispatch_evidence=dispatch_evidence,
            prompt_sha256=str(item.get("prompt_sha256") or ""),
            semantic_readback=(
                item.get("semantic_readback")
                if isinstance(item.get("semantic_readback"), dict)
                else None
            ),
        )
        warning_count = len(visual.get("warning_checks", [])) if isinstance(visual.get("warning_checks"), list) else 0
        evidence = (
            f"合規背景 worker 回傳 {FORMAL_INFOGRAPHIC_PROVIDER} / {FORMAL_INFOGRAPHIC_MODEL}；PNG SHA-256 `{item['image_sha256']}` 已讀回綁定；機械可讀性檢查通過，非阻塞警告 {warning_count} 項。"
            if outcome == "accepted"
            else f"gpt-image-2 PNG 機械檢查未通過；阻斷原因:{failure}。"
        )
        updated_blocks[index - 1] = update_infographic_validation_block(
            updated_blocks[index - 1], status="passed" if outcome == "accepted" else "needs_review", evidence=evidence
        )
        results.append({"asset_index": index, "outcome": outcome, "failure": failure, "visual_qc": visual, "record": record})
    updated_text = text
    for old_block, new_block in zip(blocks, updated_blocks):
        updated_text = updated_text.replace(old_block, new_block, 1)
    source_slice.write_text(updated_text, encoding="utf-8")
    accepted_count = sum(1 for item in results if item["outcome"] == "accepted")
    action["state"] = "completed" if accepted_count == len(results) else "blocking_failure"
    action["completed_by"] = "imagegen-complete"
    write_json_atomically(action_path, action)
    result = {
        "status": "ready" if accepted_count == len(results) else "needs_review",
        "provider": FORMAL_INFOGRAPHIC_PROVIDER,
        "model": FORMAL_INFOGRAPHIC_MODEL,
        "action_envelope": str(action_path.relative_to(root)),
        "accepted_count": accepted_count,
        "blocking_count": len(results) - accepted_count,
        "results": results,
        "imagegen_wait_seconds": float(wait_seconds),
        "imagegen_wait_evidence": wait_evidence,
        "next_action": "ingest" if accepted_count == len(results) else "one failed-page-only blocking_retry after a strict blocking failure",
    }
    print_payload(result, args.json)
    return 0 if result["status"] == "ready" else 1


def imagegen_attempt_ledger_path(source_slice: Path) -> Path:
    return source_slice.parent / "imagegen_attempts.json"


def imagegen_attempt_lock_path(source_slice: Path) -> Path:
    return source_slice.parent / ".imagegen_attempts.lock"


def acquire_imagegen_attempt_lock(source_slice: Path) -> int:
    lock_path = imagegen_attempt_lock_path(source_slice)
    try:
        return os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        raise PipelineStop("needs_review", "imagegen attempt ledger is locked by another process") from exc


def release_imagegen_attempt_lock(source_slice: Path, lock_fd: int) -> None:
    os.close(lock_fd)
    try:
        imagegen_attempt_lock_path(source_slice).unlink()
    except FileNotFoundError:
        pass


def stable_infographic_lock_block(block: str) -> str:
    stable_lines = [
        line.strip()
        for line in block.splitlines()
        if not re.match(r"^-\s*(?:PNG (?:生成方式|語義驗收狀態|語義驗收證據|反審狀態|反審證據)|語義驗收|驗收句)[:：]", line.strip())
    ]
    return "\n".join(stable_lines)


def stable_infographic_lock_hash(text: str, block: str, asset_index: int, *, legacy: bool = False) -> str:
    primary_work_title = primary_work_title_for_infographic(text)
    prompt_inputs = {
        "template_version": INFOGRAPHIC_TEMPLATE_VERSION,
        "template_prompt": INFOGRAPHIC_TEMPLATE_PROMPT,
        "source_title": source_title_for_infographic(text),
        "primary_work_status": "primary" if primary_work_title else "none",
        "primary_work_title": primary_work_title,
        "original_publication_date": extract_original_publication_date(text),
        "generated_on": date.today().isoformat(),
        "page_index": asset_index,
        "page_count": len(extract_infographic_blocks(text)),
        "page_lock": (
            "\n".join(line.strip() for line in block.splitlines()
                      if not re.match(r"^-\s*PNG (?:生成方式|語義驗收狀態|語義驗收證據):", line.strip()))
            if legacy else stable_infographic_lock_block(block)
        ),
        "coverage_rows": [
            row for row in extract_infographic_coverage_rows(text) if row["page"] == asset_index
        ],
    }
    serialized = json.dumps(prompt_inputs, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def imagegen_asset_identity(source_slice: Path, asset_index: int, text: str, block: str) -> tuple[str, str]:
    lock_hash = stable_infographic_lock_hash(text, block, asset_index)
    source_identity = str(source_slice.resolve()).replace("\\", "/").casefold()
    asset_id = hashlib.sha256(f"{source_identity}|{asset_index}|{lock_hash}".encode("utf-8")).hexdigest()
    # Preserve unchanged historical receipts. Never rewrite a stored lock or
    # infer an old prompt from a now-edited review; exact legacy match only.
    if imagegen_attempt_ledger_path(source_slice).exists():
        records = load_imagegen_attempt_ledger(source_slice)["assets"]
        if asset_id not in records:
            legacy_hash = stable_infographic_lock_hash(text, block, asset_index, legacy=True)
            legacy_id = hashlib.sha256(f"{source_identity}|{asset_index}|{legacy_hash}".encode("utf-8")).hexdigest()
            record = records.get(legacy_id)
            if isinstance(record, dict) and record.get("lock_hash") == legacy_hash:
                return legacy_id, legacy_hash
    return asset_id, lock_hash


def load_imagegen_attempt_ledger(source_slice: Path) -> dict[str, object]:
    ledger_path = imagegen_attempt_ledger_path(source_slice)
    if not ledger_path.exists():
        return {
            "schema_version": "imagegen_attempts_v2",
            "source_identity": str(source_slice.resolve()).replace("\\", "/").casefold(),
            "assets": {},
        }
    try:
        payload = json.loads(ledger_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PipelineStop("needs_review", f"imagegen attempt ledger is unreadable: {exc}") from exc
    expected_source = str(source_slice.resolve()).replace("\\", "/").casefold()
    if (
        payload.get("schema_version") != "imagegen_attempts_v2"
        or payload.get("source_identity") != expected_source
        or not isinstance(payload.get("assets"), dict)
    ):
        raise PipelineStop("needs_review", "imagegen attempt ledger schema is invalid")
    return payload


def write_imagegen_attempt_ledger(source_slice: Path, ledger: dict[str, object]) -> None:
    ledger_path = imagegen_attempt_ledger_path(source_slice)
    temporary = ledger_path.with_suffix(f".{uuid.uuid4().hex}.tmp")
    temporary.write_text(json.dumps(ledger, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(ledger_path)


def imagegen_asset_context(source_slice: Path, asset_index: int) -> tuple[str, list[str], Path, str, str]:
    text = source_slice.read_text(encoding="utf-8-sig")
    blocks = extract_infographic_blocks(text)
    if not blocks:
        raise PipelineStop("needs_review", "source slice has no infographic lock draft")
    if asset_index < 1 or asset_index > len(blocks):
        raise PipelineStop("needs_review", f"asset index must be between 1 and {len(blocks)}")
    expected_asset = expected_prompt_asset_paths(source_slice, len(blocks))[asset_index - 1]
    asset_id, lock_hash = imagegen_asset_identity(source_slice, asset_index, text, blocks[asset_index - 1])
    return text, blocks, expected_asset, asset_id, lock_hash


def imagegen_attempt_command(root: Path, args: argparse.Namespace) -> int:
    source_slice = resolve_inside(root, args.source_slice)
    text, blocks, expected_asset, asset_id, lock_hash = imagegen_asset_context(source_slice, args.asset_index)
    preflight = infographic_preflight(text, blocks)
    if preflight["status"] != "ready":
        result = {
            "status": "needs_review",
            "authorized": False,
            "reason": "preflight_failed",
            "generation_allowed": False,
            "preflight": preflight,
        }
        print_payload(result, args.json)
        return 1
    _, prompts, prompt_length_issues = compile_imagegen_prompts(root, source_slice, text, blocks)
    if prompt_length_issues:
        result = {
            "status": "needs_review",
            "authorized": False,
            "reason": "prompt_length_preflight_failed",
            "generation_allowed": False,
            "preflight": {**preflight, "prompt_length_issues": prompt_length_issues},
            "prompt_lengths": [len(prompt) for prompt in prompts],
        }
        print_payload(result, args.json)
        return 1
    lock_fd = acquire_imagegen_attempt_lock(source_slice)
    try:
        ledger_path = imagegen_attempt_ledger_path(source_slice)
        if not ledger_path.exists() and expected_asset.exists():
            raise PipelineStop("needs_review", "existing PNG has no imagegen attempt ledger; explicit migration or replacement is required")
        ledger = load_imagegen_attempt_ledger(source_slice)
        assets = ledger["assets"]
        assert isinstance(assets, dict)
        # The candidate budget belongs to the page, not to a mutable brief hash.
        # A changed lock must be reconciled explicitly; never mint another
        # initial authorization by silently starting a new hash-keyed record.
        for previous_id, previous_record in assets.items():
            if (
                previous_id != asset_id
                and isinstance(previous_record, dict)
                and previous_record.get("asset_index") == args.asset_index
                and previous_record.get("attempts")
            ):
                raise PipelineStop(
                    "needs_review",
                    "infographic page has attempts under a different lock; "
                    "reconcile the existing authorization before generating again",
                )
        record = assets.get(asset_id, {"asset_index": args.asset_index, "lock_hash": lock_hash, "attempts": []})
        if not isinstance(record, dict) or record.get("lock_hash") != lock_hash or not isinstance(record.get("attempts"), list):
            raise PipelineStop("needs_review", "imagegen attempt ledger asset record is invalid")
        attempts = record["attempts"]
        assert isinstance(attempts, list)
        if any(isinstance(item, dict) and item.get("status") == "authorized" for item in attempts):
            raise PipelineStop("needs_review", "the previous imagegen authorization is still unconsumed")
        attempt_number = len(attempts) + 1
        if attempt_number > int(IMAGEGEN_GENERATION_POLICY["max_candidates_per_asset"]):
            result = {
                "status": "needs_review", "authorized": False, "reason": "candidate_limit_reached",
                "asset": str(expected_asset.relative_to(root)), "asset_id": asset_id, "attempt_count": len(attempts),
                "max_candidates_per_asset": IMAGEGEN_GENERATION_POLICY["max_candidates_per_asset"],
                "next_action": IMAGEGEN_GENERATION_POLICY["on_limit"],
                "attempt_ledger": str(ledger_path.relative_to(root)),
            }
            print_payload(result, args.json)
            return 1
        if attempt_number == 1 and (args.reason != "initial" or args.failure):
            raise PipelineStop("needs_review", "the first candidate must use reason=initial without a failure code")
        if attempt_number == 2:
            previous = attempts[-1]
            if not isinstance(previous, dict) or previous.get("status") != "blocking_failure":
                raise PipelineStop("needs_review", "a retry is allowed only after the first candidate records a blocking failure")
            if args.reason != "blocking_retry" or args.failure not in IMAGEGEN_BLOCKING_FAILURES:
                raise PipelineStop("needs_review", "the second candidate requires reason=blocking_retry and one blocking failure code")
        attempt = {
            "attempt_id": f"IMGATT-{uuid.uuid4()}", "attempt_number": attempt_number,
            "authorized_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "reason": args.reason, "failure": args.failure or "", "status": "authorized",
        }
        attempts.append(attempt)
        record["attempts"] = attempts
        assets[asset_id] = record
        write_imagegen_attempt_ledger(source_slice, ledger)
        result = {
            "status": "authorized", "authorized": True, "asset": str(expected_asset.relative_to(root)),
            "asset_id": asset_id, "attempt": attempt, "attempt_count": len(attempts),
            "remaining_candidates": int(IMAGEGEN_GENERATION_POLICY["max_candidates_per_asset"]) - len(attempts),
            "attempt_ledger": str(ledger_path.relative_to(root)),
            "blocking_record_command_template": (
                "python tools\\firecrawl_obsidian_pipeline.py imagegen-record "
                f"--source-slice {source_slice.relative_to(root)} --asset-index {args.asset_index} "
                f"--attempt-id {attempt['attempt_id']} --outcome blocking_failure "
                "--failure <blocking_failure> --json"
            ),
            "accepted_completion": "把全部頁面語義覆核寫入 batch review JSON，再使用 imagegen-finalize；不可用底層 imagegen-record 接受正式資產。",
        }
        print_payload(result, args.json)
        return 0
    finally:
        release_imagegen_attempt_lock(source_slice, lock_fd)


def consume_imagegen_attempt(
    root: Path,
    source_slice: Path,
    *,
    asset_index: int,
    attempt_id: str,
    outcome: str,
    failure: str | None,
    finalized_by: str,
    visual_qc: dict[str, object] | None = None,
    provider: str = "",
    model: str = "",
    action_id: str = "",
    imagegen_dispatch_evidence: str = "",
    prompt_sha256: str = "",
    semantic_readback: dict[str, object] | None = None,
) -> dict[str, object]:
    _, _, expected_asset, asset_id, lock_hash = imagegen_asset_context(source_slice, asset_index)
    if not expected_asset.exists() or not is_png_asset(expected_asset):
        raise PipelineStop("needs_review", "generated asset is missing or is not a PNG")
    image_sha256 = hashlib.sha256(expected_asset.read_bytes()).hexdigest()
    if outcome == "blocking_failure" and failure not in IMAGEGEN_BLOCKING_FAILURES:
        raise PipelineStop("needs_review", "blocking_failure outcome requires one blocking failure code")
    if outcome == "accepted" and failure:
        raise PipelineStop("needs_review", "accepted outcome must not include a failure code")
    if outcome == "accepted" and (provider != FORMAL_INFOGRAPHIC_PROVIDER or model != FORMAL_INFOGRAPHIC_MODEL):
        raise PipelineStop(
            "needs_review",
            f"accepted formal infographic must prove provider={FORMAL_INFOGRAPHIC_PROVIDER} "
            f"and model={FORMAL_INFOGRAPHIC_MODEL}",
        )
    lock_fd = acquire_imagegen_attempt_lock(source_slice)
    try:
        ledger = load_imagegen_attempt_ledger(source_slice)
        assets = ledger["assets"]
        assert isinstance(assets, dict)
        record = assets.get(asset_id)
        if not isinstance(record, dict) or record.get("lock_hash") != lock_hash or not isinstance(record.get("attempts"), list):
            raise PipelineStop("needs_review", "no matching imagegen authorization record exists")
        attempts = record["attempts"]
        assert isinstance(attempts, list)
        matching = [item for item in attempts if isinstance(item, dict) and item.get("attempt_id") == attempt_id]
        if len(matching) != 1:
            raise PipelineStop("needs_review", "imagegen authorization is missing, already consumed, or does not match")
        attempt = matching[0]
        if attempt.get("status") != "authorized":
            # imagegen-complete validates every response before it mutates any
            # artifact.  A crash after this ledger write but before the slice or
            # action-envelope write is therefore safely replayable only when it
            # is the exact same finalized response.
            if (
                attempt.get("status") == outcome
                and attempt.get("failure", "") == (failure or "")
                and attempt.get("image_sha256") == image_sha256
                and attempt.get("finalized_by") == finalized_by
                and attempt.get("provider") == provider
                and attempt.get("model") == model
                and attempt.get("action_id") == action_id
                and attempt.get("prompt_sha256", "") == prompt_sha256
            ):
                return {
                    "status": outcome,
                    "recorded": False,
                    "recovered_existing_attempt": True,
                    "asset": str(expected_asset.relative_to(root)),
                    "asset_id": asset_id,
                    "attempt": attempt,
                    "attempt_ledger": str(imagegen_attempt_ledger_path(source_slice).relative_to(root)),
                }
            raise PipelineStop("needs_review", "imagegen authorization is missing, already consumed, or does not match")
        attempt["status"] = outcome
        attempt["failure"] = failure or ""
        attempt["recorded_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        attempt["image_sha256"] = image_sha256
        attempt["image_size"] = expected_asset.stat().st_size
        attempt["finalized_by"] = finalized_by
        attempt["visual_qc_status"] = visual_qc.get("status") if isinstance(visual_qc, dict) else "not_run"
        attempt["provider"] = provider
        attempt["model"] = model
        attempt["action_id"] = action_id
        attempt["prompt_sha256"] = prompt_sha256
        attempt["semantic_readback"] = semantic_readback or {}
        # Keep the legacy key for historical readers while new contracts use the
        # provider-neutral evidence field.
        attempt["imagegen_dispatch_evidence"] = imagegen_dispatch_evidence
        attempt["codex_dispatch_evidence"] = imagegen_dispatch_evidence
        write_imagegen_attempt_ledger(source_slice, ledger)
        return {
            "status": outcome, "recorded": True, "asset": str(expected_asset.relative_to(root)),
            "asset_id": asset_id, "attempt": attempt,
            "attempt_ledger": str(imagegen_attempt_ledger_path(source_slice).relative_to(root)),
        }
    finally:
        release_imagegen_attempt_lock(source_slice, lock_fd)


def imagegen_record_command(root: Path, args: argparse.Namespace) -> int:
    source_slice = resolve_inside(root, args.source_slice)
    if args.outcome == "accepted":
        raise PipelineStop("needs_review", "accepted assets must be recorded by imagegen-finalize after batch semantic and visual review")
    result = consume_imagegen_attempt(
        root,
        source_slice,
        asset_index=args.asset_index,
        attempt_id=args.attempt_id,
        outcome=args.outcome,
        failure=args.failure,
        finalized_by="imagegen-record",
    )
    result["formal_ingest_allowed"] = False
    result["next_action"] = "使用 imagegen-finalize 完成整批視覺與語義驗收；底層 record 不可直接入庫"
    print_payload(result, args.json)
    return 0


def imagegen_invalidate_command(root: Path, args: argparse.Namespace) -> int:
    """Reopen one finalized candidate when a human finds a blocking visual defect.

    This is deliberately limited to the latest finalized candidate before vault
    ingest.  It preserves the first candidate's hash and prior acceptance data,
    marks that acceptance invalidated, and reopens the single permitted retry.
    """
    source_slice = resolve_inside(root, args.source_slice)
    text, blocks, expected_asset, asset_id, lock_hash = imagegen_asset_context(source_slice, args.asset_index)
    if not expected_asset.exists() or not is_png_asset(expected_asset):
        raise PipelineStop("needs_review", "cannot invalidate a missing or non-PNG candidate")
    evidence = str(args.evidence or "").strip()
    if len(evidence) < 12:
        raise PipelineStop("needs_review", "human invalidation requires concrete visual evidence")
    lock_fd = acquire_imagegen_attempt_lock(source_slice)
    try:
        ledger = load_imagegen_attempt_ledger(source_slice)
        assets = ledger["assets"]
        assert isinstance(assets, dict)
        record = assets.get(asset_id)
        if not isinstance(record, dict) or record.get("lock_hash") != lock_hash or not isinstance(record.get("attempts"), list):
            raise PipelineStop("needs_review", "no matching finalized imagegen candidate exists")
        attempts = record["attempts"]
        assert isinstance(attempts, list)
        latest = attempts[-1] if attempts else None
        if (
            not isinstance(latest, dict)
            or latest.get("status") != "accepted"
            or latest.get("finalized_by") != "imagegen-finalize"
        ):
            raise PipelineStop("needs_review", "only the latest imagegen-finalize acceptance can be invalidated")
        latest["status"] = "blocking_failure"
        latest["failure"] = args.failure
        latest["invalidated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        latest["invalidation_evidence"] = evidence
        latest["invalidated_by"] = "human_visual_review"
        write_imagegen_attempt_ledger(source_slice, ledger)
    finally:
        release_imagegen_attempt_lock(source_slice, lock_fd)

    updated_blocks = list(blocks)
    updated_blocks[args.asset_index - 1] = update_infographic_validation_block(
        updated_blocks[args.asset_index - 1],
        status="needs_review",
        evidence=f"人手視覺覆核拒絕候選：{evidence}；阻斷原因:{args.failure}。",
    )
    updated_text = text
    for old_block, new_block in zip(blocks, updated_blocks):
        updated_text = updated_text.replace(old_block, new_block, 1)
    source_slice.write_text(updated_text, encoding="utf-8")
    result = {
        "status": "needs_review",
        "invalidated": True,
        "asset": str(expected_asset.relative_to(root)),
        "asset_id": asset_id,
        "failure": args.failure,
        "evidence": evidence,
        "next_action": "request one blocking_retry imagegen attempt for this asset",
        "attempt_ledger": str(imagegen_attempt_ledger_path(source_slice).relative_to(root)),
    }
    print_payload(result, args.json)
    return 0


def run_infographic_visual_qc(image: Path) -> dict[str, object]:
    completed = subprocess.run(
        [sys.executable, str(INFOGRAPHIC_VISUAL_QC), "--image", str(image), "--json"],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
    )
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise PipelineStop("needs_review", f"infographic visual QC returned invalid JSON: {exc}") from exc
    return payload


def visual_failure_code(payload: dict[str, object]) -> str:
    failed = payload.get("failed_checks") if isinstance(payload.get("failed_checks"), list) else []
    names = {str(item.get("name")) for item in failed if isinstance(item, dict)}
    if any("footer" in name for name in names):
        return "missing_footer"
    return "cropped_content"


def update_infographic_validation_block(block: str, *, status: str, evidence: str) -> str:
    replacements = {
        "PNG 生成方式": "gpt-image-2 直接生成 PNG（經合規背景 worker）",
        "PNG 語義驗收狀態": status,
        "PNG 語義驗收證據": evidence,
    }
    updated = block
    for label, value in replacements.items():
        pattern = rf"^- {re.escape(label)}:.*$"
        replacement = f"- {label}:{value}"
        if re.search(pattern, updated, flags=re.MULTILINE):
            updated = re.sub(pattern, replacement, updated, count=1, flags=re.MULTILINE)
        else:
            updated += f"\n{replacement}"
    return updated


def imagegen_finalize_command(root: Path, args: argparse.Namespace) -> int:
    source_slice = resolve_inside(root, args.source_slice)
    review_file = resolve_inside(root, args.review_file)
    try:
        review_payload = json.loads(review_file.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PipelineStop("needs_review", f"imagegen review file is unreadable: {exc}") from exc
    reviews = review_payload.get("assets") if isinstance(review_payload, dict) else None
    if not isinstance(reviews, list):
        raise PipelineStop("needs_review", "review file must contain an assets list")
    text = source_slice.read_text(encoding="utf-8-sig")
    blocks = extract_infographic_blocks(text)
    if not blocks:
        raise PipelineStop("needs_review", "source slice has no infographic lock draft")
    preflight = infographic_preflight(text, blocks)
    if preflight["status"] != "ready":
        raise PipelineStop("needs_review", "infographic preflight no longer matches the finalized source slice")
    expected_assets = expected_prompt_asset_paths(source_slice, len(blocks))
    by_index = {
        int(item.get("asset_index", 0)): item
        for item in reviews
        if isinstance(item, dict) and str(item.get("asset_index", "")).isdigit()
    }
    valid_indexes = set(range(1, len(blocks) + 1))
    if not reviews or len(reviews) != len(by_index) or not set(by_index).issubset(valid_indexes):
        raise PipelineStop("needs_review", "review file must contain at most one valid review per infographic page")
    ledger = load_imagegen_attempt_ledger(source_slice)
    ledger_assets = ledger["assets"]
    assert isinstance(ledger_assets, dict)
    for index, (block, expected_asset) in enumerate(zip(blocks, expected_assets), start=1):
        if index in by_index:
            continue
        asset_id, lock_hash = imagegen_asset_identity(source_slice, index, text, block)
        ledger_record = ledger_assets.get(asset_id)
        attempts = ledger_record.get("attempts") if isinstance(ledger_record, dict) else None
        latest = attempts[-1] if isinstance(attempts, list) and attempts else None
        already_accepted = (
            isinstance(ledger_record, dict)
            and ledger_record.get("lock_hash") == lock_hash
            and isinstance(latest, dict)
            and latest.get("status") == "accepted"
            and latest.get("finalized_by") == "imagegen-finalize"
            and latest.get("visual_qc_status") == "ready"
            and latest.get("provider") == FORMAL_INFOGRAPHIC_PROVIDER
            and latest.get("model") == FORMAL_INFOGRAPHIC_MODEL
            and expected_asset.exists()
            and hashlib.sha256(expected_asset.read_bytes()).hexdigest() == latest.get("image_sha256")
            and infographic_field(block, "PNG 語義驗收狀態") == "passed"
        )
        still_blocking = (
            isinstance(ledger_record, dict)
            and ledger_record.get("lock_hash") == lock_hash
            and isinstance(latest, dict)
            and latest.get("status") == "blocking_failure"
        )
        if not already_accepted and not still_blocking:
            raise PipelineStop("needs_review", f"page {index} needs a review because it is not already batch-finalized and accepted")
    prepared: list[dict[str, object]] = []
    for index in sorted(by_index):
        expected_asset = expected_assets[index - 1]
        review = by_index[index]
        attempt_id = str(review.get("attempt_id", "")).strip()
        semantic_status = str(review.get("semantic_status", "")).strip()
        semantic_evidence = str(review.get("semantic_evidence", "")).strip()
        failure = str(review.get("failure", "")).strip()
        provider = str(review.get("provider", "")).strip()
        model = str(review.get("model", "")).strip()
        if not attempt_id or semantic_status not in {"passed", "blocking_failure"} or not semantic_evidence:
            raise PipelineStop("needs_review", f"page {index} review is incomplete")
        if semantic_status == "passed" and (provider != FORMAL_INFOGRAPHIC_PROVIDER or model != FORMAL_INFOGRAPHIC_MODEL):
            raise PipelineStop(
                "needs_review",
                f"page {index} must prove provider={FORMAL_INFOGRAPHIC_PROVIDER} "
                f"and model={FORMAL_INFOGRAPHIC_MODEL}",
            )
        if semantic_status == "blocking_failure" and failure not in IMAGEGEN_BLOCKING_FAILURES:
            raise PipelineStop("needs_review", f"page {index} blocking review requires a valid failure code")
        asset_id, lock_hash = imagegen_asset_identity(source_slice, index, text, blocks[index - 1])
        ledger_record = ledger_assets.get(asset_id)
        attempts = ledger_record.get("attempts") if isinstance(ledger_record, dict) else None
        matching_attempts = [
            item for item in attempts or []
            if isinstance(item, dict) and item.get("attempt_id") == attempt_id and item.get("status") == "authorized"
        ]
        if (
            not isinstance(ledger_record, dict)
            or ledger_record.get("lock_hash") != lock_hash
            or len(matching_attempts) != 1
        ):
            raise PipelineStop("needs_review", f"page {index} has no matching unconsumed generation authorization")
        visual = run_infographic_visual_qc(expected_asset)
        outcome = "accepted" if semantic_status == "passed" and visual.get("status") == "ready" else "blocking_failure"
        record_failure = "" if outcome == "accepted" else (failure or visual_failure_code(visual))
        prepared.append({
            "asset_index": index,
            "attempt_id": attempt_id,
            "semantic_evidence": semantic_evidence,
            "visual": visual,
            "outcome": outcome,
            "failure": record_failure,
            "provider": provider,
            "model": model,
        })
    results: list[dict[str, object]] = []
    updated_blocks = list(blocks)
    for item in prepared:
        index = int(item["asset_index"])
        record_payload = consume_imagegen_attempt(
            root,
            source_slice,
            asset_index=index,
            attempt_id=str(item["attempt_id"]),
            outcome=str(item["outcome"]),
            failure=str(item["failure"]) or None,
            finalized_by="imagegen-finalize",
            visual_qc=item["visual"] if isinstance(item["visual"], dict) else None,
            provider=str(item["provider"]),
            model=str(item["model"]),
        )
        warning_count = len(item["visual"].get("warning_checks", [])) if isinstance(item["visual"], dict) else 0
        evidence = (
            f"{item['semantic_evidence']}；自動視覺安全檢查通過，非阻塞警告 {warning_count} 項；PNG SHA-256 已綁定。"
            if item["outcome"] == "accepted"
            else f"{item['semantic_evidence']}；阻斷原因:{item['failure']}。"
        )
        updated_blocks[index - 1] = update_infographic_validation_block(
            updated_blocks[index - 1],
            status="passed" if item["outcome"] == "accepted" else "needs_review",
            evidence=evidence,
        )
        results.append({
            "asset_index": index,
            "outcome": item["outcome"],
            "failure": item["failure"],
            "visual_qc": item["visual"],
            "record": record_payload,
        })
    updated_text = text
    for old_block, new_block in zip(blocks, updated_blocks):
        updated_text = updated_text.replace(old_block, new_block, 1)
    source_slice.write_text(updated_text, encoding="utf-8")
    update_current_action_after_finalize(root, source_slice, results)
    final_ledger = load_imagegen_attempt_ledger(source_slice)
    final_assets = final_ledger["assets"]
    assert isinstance(final_assets, dict)
    accepted_count = 0
    blocking_count = 0
    for index, (block, expected_asset) in enumerate(zip(updated_blocks, expected_assets), start=1):
        asset_id, lock_hash = imagegen_asset_identity(source_slice, index, updated_text, block)
        ledger_record = final_assets.get(asset_id)
        attempts = ledger_record.get("attempts") if isinstance(ledger_record, dict) else None
        latest = attempts[-1] if isinstance(attempts, list) and attempts else None
        accepted = (
            isinstance(ledger_record, dict)
            and ledger_record.get("lock_hash") == lock_hash
            and isinstance(latest, dict)
            and latest.get("status") == "accepted"
            and latest.get("finalized_by") == "imagegen-finalize"
            and latest.get("visual_qc_status") == "ready"
            and latest.get("provider") == FORMAL_INFOGRAPHIC_PROVIDER
            and latest.get("model") == FORMAL_INFOGRAPHIC_MODEL
            and expected_asset.exists()
            and hashlib.sha256(expected_asset.read_bytes()).hexdigest() == latest.get("image_sha256")
            and infographic_field(block, "PNG 語義驗收狀態") == "passed"
        )
        accepted_count += int(accepted)
        blocking_count += int(not accepted)
    ready = accepted_count == len(blocks)
    result = {
        "status": "ready" if ready else "needs_review",
        "source_slice": str(source_slice.relative_to(root)),
        "page_count": len(blocks),
        "accepted_count": accepted_count,
        "blocking_count": blocking_count,
        "results": results,
        "next_action": "ingest" if ready else "只為阻斷頁使用唯一一次針對性重生",
    }
    print_payload(result, args.json)
    return 0 if ready else 1


def validate_imagegen_attempts_for_ingest(root: Path, source_slice: Path) -> dict[str, object]:
    packet_contract = ""
    for packet_name in ("source_capture_metadata.json", "review_packet.json"):
        packet_path = source_slice.parent / packet_name
        if not packet_path.exists():
            continue
        try:
            packet = json.loads(packet_path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError) as exc:
            raise PipelineStop("needs_review", f"{packet_name} is unreadable") from exc
        if isinstance(packet, dict) and packet.get("infographic_generation_contract"):
            value = str(packet["infographic_generation_contract"])
            if packet_contract and value != packet_contract:
                raise PipelineStop("needs_review", "prepare packets disagree on infographic generation contract")
            packet_contract = value
    if packet_contract and packet_contract != INFOGRAPHIC_GENERATION_CONTRACT:
        raise PipelineStop(
            "needs_review",
            f"new source packet does not declare the {INFOGRAPHIC_GENERATION_CONTRACT} infographic contract",
        )
    text = source_slice.read_text(encoding="utf-8-sig")
    blocks = extract_infographic_blocks(text)
    _, current_prompts, prompt_issues = compile_imagegen_prompts(
        root,
        source_slice,
        text,
        blocks,
    )
    if prompt_issues:
        raise PipelineStop(
            "needs_review",
            "current infographic prompt exceeds the formal prompt contract",
        )
    methods = {infographic_field(block, "PNG 生成方式") for block in blocks}
    required_method = "gpt-image-2 直接生成 PNG（經合規背景 worker）"
    legacy_method = "Codex 內建 image_gen (gpt-image-2) 直接生成 PNG"
    if methods != {required_method} and not (not packet_contract and methods == {legacy_method}):
        raise PipelineStop("needs_review", "all formal infographic blocks must use gpt-image-2 through a compatible background worker; HTML/SVG/CSS/template PNG is not accepted")
    expected_assets = expected_prompt_asset_paths(source_slice, len(blocks))
    ledger_path = imagegen_attempt_ledger_path(source_slice)
    if not ledger_path.exists():
        raise PipelineStop("needs_review", "formal ingest requires a persisted imagegen attempt ledger")
    ledger = load_imagegen_attempt_ledger(source_slice)
    assets = ledger["assets"]
    assert isinstance(assets, dict)
    verified: list[dict[str, object]] = []
    for index, (block, expected_asset) in enumerate(zip(blocks, expected_assets), start=1):
        asset_id, lock_hash = imagegen_asset_identity(source_slice, index, text, block)
        record = assets.get(asset_id)
        if not isinstance(record, dict) or record.get("lock_hash") != lock_hash or not isinstance(record.get("attempts"), list):
            raise PipelineStop("needs_review", f"asset {index} has no matching imagegen authorization record")
        attempts = record["attempts"]
        assert isinstance(attempts, list)
        if not 1 <= len(attempts) <= int(IMAGEGEN_GENERATION_POLICY["max_candidates_per_asset"]):
            raise PipelineStop("needs_review", f"asset {index} has an invalid candidate count")
        latest = attempts[-1]
        if not isinstance(latest, dict) or latest.get("status") != "accepted":
            raise PipelineStop("needs_review", f"asset {index} has no accepted, consumed imagegen authorization")
        accepted_by_new_contract = (
            latest.get("finalized_by") == "imagegen-complete"
            and bool(latest.get("action_id"))
            and bool(latest.get("imagegen_dispatch_evidence") or latest.get("codex_dispatch_evidence"))
        )
        accepted_by_batch_finalize_contract = latest.get("finalized_by") == "imagegen-finalize"
        if not (accepted_by_new_contract or accepted_by_batch_finalize_contract) or latest.get("visual_qc_status") != "ready":
            raise PipelineStop("needs_review", f"asset {index} has not completed formal gpt-image-2 PNG/readback validation")
        if latest.get("provider") != FORMAL_INFOGRAPHIC_PROVIDER or latest.get("model") != FORMAL_INFOGRAPHIC_MODEL:
            raise PipelineStop(
                "needs_review",
                f"asset {index} does not prove provider={FORMAL_INFOGRAPHIC_PROVIDER} "
                f"and model={FORMAL_INFOGRAPHIC_MODEL}",
            )
        if infographic_field(block, "PNG 語義驗收狀態") != "passed":
            raise PipelineStop("needs_review", f"asset {index} source slice does not record passed batch semantic review")
        if not expected_asset.exists() or hashlib.sha256(expected_asset.read_bytes()).hexdigest() != latest.get("image_sha256"):
            raise PipelineStop("needs_review", f"asset {index} PNG hash does not match its consumed authorization")
        prompt_sha256 = str(latest.get("prompt_sha256") or "")
        if prompt_sha256:
            current_prompt = current_prompts[index - 1]
            if hashlib.sha256(current_prompt.encode("utf-8")).hexdigest() != prompt_sha256:
                raise PipelineStop("needs_review", f"asset {index} no longer matches its accepted prompt")
            readback = latest.get("semantic_readback")
            readback_errors = image_semantic_readback_errors(
                readback if isinstance(readback, dict) else None,
                png_sha256=str(latest.get("image_sha256") or ""),
                prompt=current_prompt,
            )
            if readback_errors:
                raise PipelineStop(
                    "needs_review",
                    f"asset {index} image semantic readback failed: {'; '.join(readback_errors)}",
                )
        verified.append({
            "asset_index": index,
            "asset_id": asset_id,
            "attempt_count": len(attempts),
            "provider": latest["provider"],
            "model": latest["model"],
            "action_id": latest.get("action_id", ""),
            "image_sha256": latest["image_sha256"],
        })
    return {
        "status": "ready",
        "attempt_ledger": str(ledger_path.relative_to(root)),
        "provider": FORMAL_INFOGRAPHIC_PROVIDER,
        "model": FORMAL_INFOGRAPHIC_MODEL,
        "assets": verified,
    }


def ingest_command(root: Path, args: argparse.Namespace) -> int:
    source_slice = resolve_inside(root, args.source_slice)
    vault = resolve_inside(root, args.vault)
    dry_run = run_adapter(root, source_slice, vault, args.max_kp, "--json")
    dry_payload = parse_json_stdout(dry_run, "adapter dry-run")
    result: dict[str, object] = {
        "status": "ready" if dry_run.returncode == 0 else "needs_review",
        "stage": "dry_run",
        "source_slice": str(source_slice.relative_to(root)),
        "dry_run": dry_payload,
        "validation_manifest": workflow_manifest("url-ingest", root),
    }
    if dry_run.returncode != 0:
        return finish_ingest_command(root, args, result, 1)

    imagegen_attempts = validate_imagegen_attempts_for_ingest(root, source_slice)
    result["imagegen_attempts"] = imagegen_attempts

    if not args.write:
        return finish_ingest_command(root, args, result, 0)

    write_run = run_adapter(root, source_slice, vault, args.max_kp, "--write", "--verify", "--source-local", "--json")
    write_payload = parse_json_stdout(write_run, "adapter write/readback")
    result["stage"] = "write"
    result["write"] = write_payload
    if write_run.returncode != 0:
        result["status"] = "needs_review"
        return finish_ingest_command(root, args, result, 1)

    source_id = source_id_from_write_payload(write_payload)
    source_local_payload = source_local_readback(write_payload, source_id)
    result["source_local_readback"] = source_local_payload
    if source_local_payload["status"] != "ready":
        result["status"] = "needs_review"
        return finish_ingest_command(root, args, result, 1)

    outcome_run = run_outcome_generator(root, vault, source_id)
    outcome_payload = parse_json_stdout(outcome_run, "outcome page generator")
    result["stage"] = "incremental_outcome"
    result["outcome_pages"] = outcome_payload
    if outcome_run.returncode != 0:
        result["status"] = "needs_review"
        return finish_ingest_command(root, args, result, 1)
    full_vault_qc: dict[str, object] | None = None
    if args.full_vault_qc:
        health_run = run_adapter(root, source_slice, vault, args.max_kp, "--vault-health", "--json")
        duplicate_run = run_duplicate_review(root, vault)
        full_vault_qc = {
            "vault_health": parse_json_stdout(health_run, "vault health"),
            "duplicate_review": parse_json_stdout(duplicate_run, "duplicate review"),
        }
        result["full_vault_qc"] = full_vault_qc
    source_ingest_job = None
    if args.source_ingest_job_id:
        source_ingest_job = read_source_ingest_job_for_completion(
            root,
            args.source_ingest_job_root,
            args.source_ingest_job_id,
            write_payload,
        )

    completion_report = build_completion_report(
        root,
        dry_payload,
        write_payload,
        outcome_payload,
        source_local_payload,
        source_ingest_job,
        full_vault_qc,
    )
    result["completion_report"] = completion_report
    result["status"] = completion_report["status"]
    return finish_ingest_command(root, args, result, 0 if completion_report["status"] == "ready" else 1)


def finish_ingest_command(root: Path, args: argparse.Namespace, result: dict[str, object], exit_code: int) -> int:
    if getattr(args, "completion_report_out", None):
        output_path = resolve_inside(root, args.completion_report_out)
        result["completion_report_out"] = str(output_path.relative_to(root))
        write_completion_report_out(root, args.completion_report_out, result)
    print_payload(result, args.json)
    return exit_code


def write_completion_report_out(root: Path, output: Path, result: dict[str, object]) -> Path:
    target = resolve_inside(root, output)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
    payload = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True)
    temporary.write_text(payload + "\n", encoding="utf-8")
    os.replace(temporary, target)
    readback = json.loads(target.read_text(encoding="utf-8-sig"))
    if readback != result:
        raise PipelineStop("blocked", "completion report output readback did not match written result")
    return target


def build_completion_report(
    root: Path,
    dry_payload: dict[str, object],
    write_payload: dict[str, object],
    outcome_payload: dict[str, object],
    source_local_payload: dict[str, object],
    source_ingest_job: dict[str, object] | None = None,
    full_vault_qc: dict[str, object] | None = None,
) -> dict[str, object]:
    source_id = source_id_from_write_payload(write_payload)
    evidence = {
        "source_note": existing_relative_path(root, write_payload.get("source_note")),
        "application_note": existing_relative_path(root, write_payload.get("application_note")),
        "knowledge_points": existing_relative_paths(root, write_payload.get("knowledge_points")),
        "asset": existing_relative_path(root, write_payload.get("asset")),
        "assets": existing_relative_paths(root, write_payload.get("assets")),
        "obsidian_portal": existing_relative_path(root, outcome_payload.get("obsidian_portal")),
        "html_index": existing_relative_path(root, outcome_payload.get("html_index")),
        "detail_page": matching_detail_page(root, outcome_payload.get("pages"), source_id),
    }
    verification = write_payload.get("verification") if isinstance(write_payload.get("verification"), dict) else {}
    full_body_evidence = verification.get("full_body_evidence") if isinstance(verification, dict) else {}
    if isinstance(full_body_evidence, dict) and full_body_evidence.get("mode") == "full_body_required":
        evidence["full_body"] = full_body_evidence
    if source_ingest_job is not None:
        evidence["source_ingest_job_pre_completion"] = source_ingest_job
    checks = [
        check_row("adapter_dry_run", dry_payload.get("status") == "ready", "adapter dry-run ready"),
        check_row("write_readback", verification_passed(write_payload), "adapter write/readback passed"),
        check_row(
            "full_body_readback",
            full_body_completion_passed(write_payload),
            "full-body mode, canonical body SHA-256 and representative anchors read back when required",
        ),
        check_row(
            "source_local_readback",
            source_local_payload.get("status") == "ready",
            "current source identity, registry and written artifacts read back",
        ),
        check_row("incremental_outcome", outcome_payload.get("status") == "ready", "current outcome page and index updated"),
        check_row(
            "user_entrances",
            bool(evidence["obsidian_portal"] and evidence["html_index"] and evidence["detail_page"]),
            "Obsidian portal, HTML index and detail page read back",
        ),
    ]
    if full_vault_qc is not None:
        health_payload = full_vault_qc.get("vault_health")
        duplicate_payload = full_vault_qc.get("duplicate_review")
        if isinstance(health_payload, dict):
            checks.append(check_row("full_vault_health", health_payload.get("status") == "passed", "opt-in vault health passed"))
        if isinstance(duplicate_payload, dict):
            duplicate_count = int(duplicate_payload.get("candidate_count") or 0)
            checks.append(check_row(
                "full_vault_duplicate_review",
                duplicate_payload.get("status") == "ready" and duplicate_count == 0,
                f"opt-in duplicate review status is {duplicate_payload.get('status')}; candidate count is {duplicate_count}",
            ))
    if source_ingest_job is not None:
        checks.append(
            check_row(
                "source_ingest_job_pre_completion_readback",
                source_ingest_job.get("status") == "ready",
                f"source-ingest job pre-completion readback status is {source_ingest_job.get('status')}",
            )
        )
    blocked_reasons = [
        {"check": row["check"], "message": row["message"]}
        for row in checks
        if row["status"] != "passed"
    ]
    return {
        "status": "ready" if not blocked_reasons else "needs_review",
        "source_id": source_id,
        "checks": checks,
        "evidence": evidence,
        "blocked_reasons": blocked_reasons,
    }


def source_local_readback(write_payload: dict[str, object], source_id: str) -> dict[str, object]:
    verification = write_payload.get("verification")
    if not isinstance(verification, dict):
        return {"status": "needs_review", "reason": "writer verification is missing"}
    required = (
        "source_note_exists",
        "source_identity_present",
        "registry_has_source_identity",
        "registry_points_to_source_note",
        "registry_source_identity_matches",
        "registry_source_id_matches",
        "asset_exists",
    )
    missing = [key for key in required if verification.get(key) is not True]
    if not source_id:
        missing.append("source_id")
    return {
        "status": "ready" if not missing else "needs_review",
        "source_id": source_id,
        "source_identity": write_payload.get("source_identity", ""),
        "missing_or_failed": missing,
    }


def read_source_ingest_job_for_completion(
    root: Path,
    job_root: Path,
    job_id: str,
    write_payload: dict[str, object],
) -> dict[str, object]:
    source_identity = str(write_payload.get("source_identity") or "")
    resolved_job_root = resolve_inside(root, job_root)
    job = source_ingest_latest_jobs(resolved_job_root).get(job_id)
    if not job:
        return {
            "status": "missing",
            "job_id": job_id,
            "source_identity": source_identity,
            "reason": "source-ingest job not found",
        }
    public = public_source_ingest_job(job)
    if public.get("source_identity") != source_identity:
        return {
            "status": "mismatch",
            "job_id": job_id,
            "source_identity": public.get("source_identity"),
            "expected_source_identity": source_identity,
            "job": public,
        }
    return {
        "status": "ready",
        "job_id": job_id,
        "source_identity": source_identity,
        "job": public,
    }


def check_row(name: str, passed: bool, message: str) -> dict[str, str]:
    return {
        "check": name,
        "status": "passed" if passed else "needs_review",
        "message": message,
    }


def verification_passed(payload: dict[str, object]) -> bool:
    verification = payload.get("verification")
    return isinstance(verification, dict) and verification.get("passed") is True


def full_body_completion_passed(payload: dict[str, object]) -> bool:
    verification = payload.get("verification")
    if not isinstance(verification, dict):
        return False
    evidence = verification.get("full_body_evidence")
    if not isinstance(evidence, dict) or evidence.get("mode") != "full_body_required":
        return True
    return (
        verification.get("full_body_readback") is True
        and evidence.get("canonical_body_sha256")
        and evidence.get("canonical_body_sha256") == evidence.get("readback_sha256")
        and bool(evidence.get("representative_anchors"))
        and evidence.get("representative_anchors_present") is True
    )


def source_id_from_write_payload(payload: dict[str, object]) -> str:
    for value in (payload.get("source_note"), payload.get("application_note")):
        match = re.search(r"(KS-VS-\d{4})", str(value or ""))
        if match:
            return match.group(1)
    return ""


def existing_relative_path(root: Path, value: object) -> str | None:
    if value is None:
        return None
    path = Path(str(value))
    if not path.is_absolute():
        path = root / path
    try:
        resolved = path.resolve()
        if root != resolved and root not in resolved.parents:
            return None
        if not resolved.exists():
            return None
        return str(resolved.relative_to(root))
    except (OSError, ValueError):
        return None


def existing_relative_paths(root: Path, value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    paths = []
    for item in value:
        existing = existing_relative_path(root, item)
        if existing:
            paths.append(existing)
    return paths


def matching_detail_page(root: Path, value: object, source_id: str) -> str | None:
    if not isinstance(value, list) or not source_id:
        return None
    for item in value:
        if source_id not in str(item):
            continue
        existing = existing_relative_path(root, item)
        if existing:
            return existing
    return None


def workflow_manifest(scenario: str, root: Path) -> dict[str, object]:
    try:
        return build_embedded_manifest(scenario, root=root)
    except Exception as exc:  # pragma: no cover - defensive reporting for CLI surface
        return {
            "status": "needs_review",
            "scenario": scenario,
            "blocked_reasons": [{"reason": "manifest_build_failed", "message": str(exc)}],
        }


def capture_quality_payload(quality: CaptureQuality) -> dict[str, object]:
    return {
        "status": quality.status,
        "issues": quality.issues,
        "signals": quality.signals,
        "long_form_segments": quality.long_form_segments,
        "infographic_planning": quality.infographic_planning,
    }


def capture_review_gate(quality: CaptureQuality, source_input_kind: str = "url") -> dict[str, object]:
    completed_status = {
        "user_provided_text": "user_text_complete",
        "user_provided_image": "user_image_complete",
    }.get(source_input_kind, "public_ok")
    capture_reference = "source_capture.md" if source_input_kind != "user_provided_image" else "原始圖片與 image_analysis.json"
    reasons = [
        {
            "code": "source_capture_must_be_reviewed",
            "message": f"必須對照 {capture_reference} 確認來源完整,才可把待審狀態改為 {completed_status}。",
        }
    ]
    for issue in quality.issues:
        reasons.append(
            {
                "code": issue["code"],
                "message": issue["message"],
            }
        )
    if quality.long_form_segments:
        reasons.append(
            {
                "code": "long_form_segments_must_be_checked",
                "message": "長文必須逐段核對 long_form_segments,不可只讀摘錄或只抽少量重點。",
            }
        )
    if quality.infographic_planning.get("needs_multiple_images"):
        reasons.append(
            {
                "code": "multi_infographic_decision_must_be_checked",
                "message": "工具初判需要多張資訊圖時,必須確認每張圖的任務與分工,不可用單圖硬塞。",
            }
        )
    return {
        "status": "blocked" if quality.status == "blocked" else "needs_review",
        "draft_slice_allowed": quality.status != "blocked",
        "formal_write_allowed": False,
        "review_required": True,
        "review_required_reasons": reasons,
        "next_state": "AI 審完 capture、補完 source slice、把可讀性與 AI 分析狀態改為可正式入庫後,才可進入 ingest。",
    }


def analyze_capture_quality(capture: UrlCapture, requested_url: str, main_markdown: str) -> CaptureQuality:
    main_text = meaningful_text(main_markdown)
    headings = markdown_headings(main_markdown)
    image_urls = capture_image_urls(capture, main_markdown)
    fallback_attempts = capture.metadata.get("fallback_attempts")
    fallback_used = (
        isinstance(fallback_attempts, list)
        and len(fallback_attempts) > 1
        and any(isinstance(item, dict) and item.get("status") != "captured" for item in fallback_attempts[:-1])
    )
    source_seen = source_url_seen_in_text(requested_url, capture.markdown) if requested_url else None
    long_form = len(main_markdown) >= LONG_FORM_MAIN_CHARS or len(headings) >= LONG_FORM_HEADING_COUNT
    segments = segment_main_markdown(main_markdown) if long_form else []
    issues: list[dict[str, str]] = []
    if capture.status_code is not None and capture.status_code >= 400:
        issues.append(
            {
                "severity": "blocked",
                "code": "http_error_status",
                "message": f"來源回傳 HTTP {capture.status_code}，不可當成完整正文。",
                "recovery": "改用可讀來源、Tavily 或瀏覽器文字匯出後重跑 prepare。",
            }
        )
    if len(main_text) < BLOCKED_MIN_MAIN_CHARS:
        issues.append(
            {
                "severity": "blocked",
                "code": "main_content_too_short",
                "message": "清理導覽與頁尾後，主體內容太短，可能只是登入提示、錯誤頁或截斷片段。",
                "recovery": "先用另一個擷取來源，或用 browser-file 匯出可見正文，再重跑 prepare。",
            }
        )
    elif len(main_text) < REVIEW_MIN_MAIN_CHARS:
        issues.append(
            {
                "severity": "needs_review",
                "code": "main_content_short",
                "message": "正文可讀但偏短，審稿時必須核對是否漏了主要段落。",
                "recovery": "對照 source_capture.md 與原頁可見內容；若只是片段，不可改成 public_ok。",
            }
        )
    if capture.provider == "browser-file" and not source_seen:
        issues.append(
            {
                "severity": "needs_review",
                "code": "browser_capture_source_not_embedded",
                "message": "瀏覽器匯出文字內未偵測到原 URL 或網域，日後追溯來源較弱。",
                "recovery": "審稿時確認檔案確實來自指定 URL；最好在 browser capture 檔首補來源 URL 與擷取時間。",
            }
        )
    if fallback_used:
        issues.append(
            {
                "severity": "needs_review",
                "code": "provider_fallback_used",
                "message": "本次擷取曾使用備用來源，需核對備用內容是否完整。",
                "recovery": "先讀 fallback_attempts，再對照 source_capture.md，不可只看最終 provider 名稱。",
            }
        )
    planning = infographic_plan(main_markdown, headings, image_urls, segments)
    status = "blocked" if any(issue["severity"] == "blocked" for issue in issues) else "needs_review"
    signals: dict[str, object] = {
        "main_text_chars": len(main_text),
        "markdown_chars": len(capture.markdown),
        "main_markdown_chars": len(main_markdown),
        "heading_count": len(headings),
        "image_count": len(image_urls),
        "long_form": long_form,
        "fallback_attempts": fallback_attempts or [],
        "fallback_used": fallback_used,
        "browser_source_url_seen": source_seen if capture.provider == "browser-file" else None,
    }
    return CaptureQuality(
        status=status,
        issues=issues,
        signals=signals,
        long_form_segments=segments,
        infographic_planning=planning,
    )


def meaningful_text(markdown: str) -> str:
    text = re.sub(r"!\[[^\]]*\]\([^)]+\)", " ", markdown)
    text = re.sub(r"\[[^\]]+\]\([^)]+\)", " ", text)
    text = re.sub(r"[#>*_`|\\-]+", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def source_url_seen_in_text(url: str, markdown: str) -> bool:
    parsed = urlsplit(url)
    candidates = {url.strip()}
    if parsed.netloc:
        candidates.add(parsed.netloc)
    lowered = markdown.casefold()
    return any(candidate and candidate.casefold() in lowered for candidate in candidates)


def capture_image_urls(capture: UrlCapture, main_markdown: str) -> list[str]:
    urls: list[str] = []
    urls.extend(re.findall(r"!\[[^\]]*\]\(([^)]+)\)", main_markdown))
    images = capture.metadata.get("images")
    if not urls and isinstance(images, list):
        urls.extend(str(image) for image in images if image)
    seen: set[str] = set()
    unique: list[str] = []
    for url in urls:
        normalized = url.strip()
        if normalized and normalized not in seen:
            seen.add(normalized)
            unique.append(normalized)
    return unique


def segment_main_markdown(markdown: str) -> list[dict[str, object]]:
    lines = markdown.splitlines()
    segments: list[dict[str, object]] = []
    current_title = "開首內容"
    current: list[str] = []
    for line in lines:
        if line.startswith("#") and current:
            segments.append(segment_payload(current_title, current))
            current = [line]
            current_title = line.strip("# ").strip() or "未命名段落"
            continue
        if line.startswith("#") and not current:
            current_title = line.strip("# ").strip() or "未命名段落"
        current.append(line)
    if current:
        segments.append(segment_payload(current_title, current))
    if len(segments) <= 1 and len(markdown) > LONG_FORM_MAIN_CHARS:
        return chunk_segments(markdown)
    return segments[:12]


def chunk_segments(markdown: str) -> list[dict[str, object]]:
    chunks: list[dict[str, object]] = []
    size = 4500
    for index in range(0, len(markdown), size):
        body = markdown[index : index + size]
        chunks.append(
            {
                "title": f"正文分段 {len(chunks) + 1}",
                "char_count": len(body),
                "excerpt": excerpt_text(body, limit=420),
            }
        )
    return chunks[:12]


def segment_payload(title: str, lines: list[str]) -> dict[str, object]:
    body = "\n".join(lines).strip()
    return {"title": title, "char_count": len(body), "excerpt": excerpt_text(body, limit=420)}


def infographic_plan(
    markdown: str, headings: list[str], image_urls: list[str], segments: list[dict[str, object]]
) -> dict[str, object]:
    reasons: list[str] = []
    recommended_count = 1
    if len(markdown) >= LONG_FORM_MAIN_CHARS or len(headings) >= LONG_FORM_HEADING_COUNT:
        recommended_count = max(recommended_count, 2)
        reasons.append("原文偏長或標題層次較多，單張圖容易漏掉重要段落。")
    if len(image_urls) >= MULTI_IMAGE_MARKDOWN_COUNT:
        recommended_count = max(recommended_count, 2)
        reasons.append("來源含多張圖片或圖像線索，需判斷是否分成內容圖與例證圖。")
    if len(segments) >= 6 and len(markdown) >= 22000:
        recommended_count = max(recommended_count, 3)
        reasons.append("長文分段較多，建議用多圖承接不同理解障礙。")
    if not reasons:
        reasons.append("目前偵測為單一主線；若 AI 審稿發現多主線，仍要改用多圖。")
    return {
        "recommended_count": min(recommended_count, 5),
        "needs_multiple_images": recommended_count > 1,
        "reasons": reasons,
    }


def with_fallback_attempts(capture: UrlCapture, attempts: list[dict[str, str]]) -> UrlCapture:
    metadata = dict(capture.metadata)
    metadata["fallback_attempts"] = attempts
    return replace(capture, metadata=metadata)


def load_capture_fixture(path: Path, provider: str, url: str) -> UrlCapture:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if provider == "firecrawl":
        return parse_firecrawl_capture(payload, url)
    if provider == "tavily":
        return parse_tavily_capture(payload, url)
    raise AssertionError(f"unsupported fixture provider: {provider}")


def load_capture(args: argparse.Namespace) -> UrlCapture:
    if args.user_text_file:
        if (
            args.capture_provider != "auto"
            or args.browser_capture_file
            or args.firecrawl_response_file
            or args.tavily_response_file
        ):
            raise PipelineStop(
                "blocked",
                "--user-text-file cannot be combined with a URL capture provider, browser export, or saved URL response fixture",
            )
        return load_user_text_capture(args.user_text_file, args.title)
    if args.user_image_file:
        if (
            args.capture_provider != "auto"
            or args.browser_capture_file
            or args.firecrawl_response_file
            or args.tavily_response_file
        ):
            raise PipelineStop(
                "blocked",
                "--user-image-file cannot be combined with a URL capture provider, browser export, or saved URL response fixture",
            )
        return load_user_image_capture(args.user_image_file, args.title)
    if args.browser_capture_file:
        return load_browser_capture(args)
    if args.capture_provider == "auto" and (args.firecrawl_response_file or args.tavily_response_file):
        attempts: list[dict[str, str]] = []
        if args.firecrawl_response_file:
            try:
                capture = load_capture_fixture(args.firecrawl_response_file, "firecrawl", args.url)
                attempts.append({"provider": "firecrawl", "status": "captured", "message": "fixture captured"})
                return with_fallback_attempts(capture, attempts)
            except PipelineStop as exc:
                attempts.append({"provider": "firecrawl", "status": exc.status, "message": exc.message})
        if args.tavily_response_file:
            try:
                capture = load_capture_fixture(args.tavily_response_file, "tavily", args.url)
                attempts.append({"provider": "tavily", "status": "captured", "message": "fixture captured"})
                return with_fallback_attempts(capture, attempts)
            except PipelineStop as exc:
                attempts.append({"provider": "tavily", "status": exc.status, "message": exc.message})
        raise PipelineStop("blocked", f"Saved response fixtures did not capture Markdown; attempts={attempts}")
    if args.firecrawl_response_file:
        return load_capture_fixture(args.firecrawl_response_file, "firecrawl", args.url)
    if args.tavily_response_file:
        return load_capture_fixture(args.tavily_response_file, "tavily", args.url)
    if args.capture_provider == "browser-file":
        raise PipelineStop("blocked", "--browser-capture-file is required when capture-provider is browser-file")
    if args.capture_provider == "firecrawl":
        return parse_firecrawl_capture(scrape_firecrawl(args.url), args.url)
    if args.capture_provider == "tavily":
        return parse_tavily_capture(extract_tavily(args.url), args.url)
    firecrawl_error = None
    attempts: list[dict[str, str]] = []
    try:
        capture = parse_firecrawl_capture(scrape_firecrawl(args.url), args.url)
        attempts.append({"provider": "firecrawl", "status": "captured", "message": "captured"})
        return with_fallback_attempts(capture, attempts)
    except PipelineStop as exc:
        firecrawl_error = exc.message
        attempts.append({"provider": "firecrawl", "status": exc.status, "message": exc.message})
    try:
        capture = parse_tavily_capture(extract_tavily(args.url), args.url)
        attempts.append({"provider": "tavily", "status": "captured", "message": "captured"})
        return with_fallback_attempts(capture, attempts)
    except PipelineStop as exc:
        attempts.append({"provider": "tavily", "status": exc.status, "message": exc.message})
        raise PipelineStop(
            "blocked",
            "Firecrawl and Tavily could not capture this URL. "
            f"Firecrawl: {firecrawl_error}; Tavily: {exc.message}. "
            "Use an authenticated browser export with --browser-capture-file.",
        ) from exc


def load_user_text_capture(path: Path, title_override: str | None) -> UrlCapture:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise PipelineStop("blocked", f"user text file is unreadable: {exc}") from exc
    if not raw:
        raise PipelineStop("blocked", "user text file is empty")
    try:
        markdown = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise PipelineStop("blocked", "user text file must be UTF-8 or UTF-8 with BOM") from exc
    if not markdown.strip():
        raise PipelineStop("blocked", "user text file has no readable text")
    return UrlCapture(
        url="",
        title=title_override or first_markdown_heading(markdown) or "使用者提供文字",
        markdown=markdown,
        source_input_kind="user_provided_text",
        source_content_sha256=hashlib.sha256(raw).hexdigest(),
        source_bytes=raw,
        status_code=None,
        metadata={"input_encoding": "utf-8", "content_bytes": len(raw)},
        provider="user-text-file",
        capture_method="使用者提供文字檔（內容 SHA-256 驗證）",
        credential_reference="none",
    )


def load_user_image_capture(path: Path, title_override: str | None) -> UrlCapture:
    try:
        inspection = inspect_image(path)
        raw = path.read_bytes()
    except ImageSourceContractError as exc:
        raise PipelineStop("blocked", str(exc)) from exc
    except OSError as exc:
        raise PipelineStop("blocked", f"image input is unreadable: {exc}") from exc
    title = title_override or path.stem or "使用者提供圖片"
    return UrlCapture(
        url="",
        title=title,
        markdown="",
        source_input_kind="user_provided_image",
        source_content_sha256=None,
        source_bytes=raw,
        status_code=None,
        metadata={
            "input_encoding": "binary",
            "content_bytes": inspection.byte_length,
            "image_mime_type": inspection.mime_type,
            "image_width": inspection.width,
            "image_height": inspection.height,
        },
        provider="user-image-file",
        capture_method="使用者提供圖片檔（原始 bytes SHA-256、MIME 與尺寸驗證）",
        credential_reference="none",
        source_image_sha256=inspection.sha256,
        source_image_mime=inspection.mime_type,
        source_image_width=inspection.width,
        source_image_height=inspection.height,
        source_image_extension=inspection.extension,
    )


def parse_firecrawl_capture(payload: dict[str, object], fallback_url: str) -> UrlCapture:
    if not payload.get("success"):
        raise PipelineStop("blocked", "Firecrawl response success is false")
    data = payload.get("data")
    if not isinstance(data, dict):
        raise PipelineStop("blocked", "Firecrawl response has no data object")
    markdown = str(data.get("markdown") or "").strip()
    if not markdown:
        raise PipelineStop("blocked", "Firecrawl response has no markdown body")
    metadata = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}
    title = str(metadata.get("title") or first_markdown_heading(markdown) or fallback_url).strip()
    source_url = str(metadata.get("sourceURL") or metadata.get("url") or fallback_url).strip()
    status_code = metadata.get("statusCode")
    return UrlCapture(
        url=source_url,
        title=title,
        markdown=markdown,
        source_input_kind="url",
        source_content_sha256=None,
        source_bytes=None,
        status_code=int(status_code) if isinstance(status_code, int) else None,
        metadata=metadata,
        provider="firecrawl",
        capture_method=f"Firecrawl API v2 scrape（markdown, onlyMainContent, timeout {DEFAULT_TIMEOUT_MS}）",
        credential_reference="FIRECRAWL_API_KEY",
    )


def capture_endpoint(capture: UrlCapture) -> str:
    if capture.source_input_kind == "user_provided_text":
        return "user-provided-text"
    if capture.source_input_kind == "user_provided_image":
        return "user-provided-image"
    if capture.provider == "firecrawl":
        return FIRECRAWL_ENDPOINT
    if capture.provider == "tavily":
        return TAVILY_EXTRACT_ENDPOINT
    return "browser-file"


def source_identity(capture: UrlCapture) -> str:
    if capture.source_input_kind == "user_provided_text":
        if not capture.source_content_sha256:
            raise AssertionError("user text capture is missing source_content_sha256")
        return f"user_provided_text:{capture.source_content_sha256}"
    if capture.source_input_kind == "user_provided_image":
        if not capture.source_image_sha256:
            raise AssertionError("user image capture is missing source_image_sha256")
        return f"user_provided_image:{capture.source_image_sha256}"
    return f"url:{hashlib.sha256(canonical_url(capture.url).encode('utf-8')).hexdigest()}"


def canonical_url(value: str) -> str:
    stripped = value.strip()
    without_fragment, _ = urldefrag(stripped)
    parsed = urlsplit(without_fragment)
    if not parsed.scheme or not parsed.netloc:
        return without_fragment.rstrip("/")
    return urlunsplit((
        parsed.scheme.lower(),
        parsed.netloc.lower(),
        parsed.path.rstrip("/") or "/",
        parsed.query,
        "",
    ))


def parse_tavily_capture(payload: dict[str, object], fallback_url: str) -> UrlCapture:
    results = payload.get("results")
    if not isinstance(results, list) or not results:
        failed = payload.get("failed_results")
        raise PipelineStop("blocked", f"Tavily extract returned no results; failed_results={failed}")
    data = results[0]
    if not isinstance(data, dict):
        raise PipelineStop("blocked", "Tavily extract result is not an object")
    markdown = str(data.get("raw_content") or data.get("content") or "").strip()
    if not markdown:
        raise PipelineStop("blocked", "Tavily extract returned no raw_content/content")
    title = str(data.get("title") or first_markdown_heading(markdown) or fallback_url).strip()
    source_url = str(data.get("url") or fallback_url).strip()
    return UrlCapture(
        url=source_url,
        title=title,
        markdown=markdown,
        source_input_kind="url",
        source_content_sha256=None,
        source_bytes=None,
        status_code=None,
        metadata={"images": data.get("images") or [], "favicon": data.get("favicon"), "usage": payload.get("usage")},
        provider="tavily",
        capture_method="Tavily Extract API（advanced, markdown）",
        credential_reference="TAVILY_API_KEY",
    )


def load_browser_capture(args: argparse.Namespace) -> UrlCapture:
    path = args.browser_capture_file
    try:
        raw = path.read_bytes()
        text = raw.decode("utf-8-sig").strip()
    except (OSError, UnicodeDecodeError) as exc:
        raise PipelineStop("blocked", f"browser capture file is unreadable UTF-8 text: {exc}") from exc
    if not text:
        raise PipelineStop("blocked", "browser capture file is empty")
    title = args.title or first_markdown_heading(text) or args.url
    return UrlCapture(
        url=args.url,
        title=title,
        markdown=text,
        source_input_kind="url",
        source_content_sha256=None,
        source_bytes=raw,
        status_code=None,
        metadata={"input_encoding": "utf-8", "content_bytes": len(raw)},
        provider="browser-file",
        capture_method="Authenticated browser text export（agent-visible page text copied to local file）",
        credential_reference="none",
    )


def validate_full_capture_body(body: str, extra_evidence: str = "") -> None:
    """Shared minimum evidence gate; passing does not prove source completeness."""
    if not isinstance(body, str) or len(body.strip()) < 200:
        raise PipelineStop("blocked", "source_body_too_short")
    if sum(character.isalnum() for character in body) < 40 or body.count("\ufffd") > len(body) * 0.05:
        raise PipelineStop("blocked", "source_body_unreadable")
    searchable = f"{extra_evidence}\n{body}".casefold()
    access_block_markers = (
        "access denied", "enable javascript to continue", "please log in",
        "please sign in", "登入以繼續", "請先登入", "verify you are human",
        "checking your browser", "unusual traffic", "captcha verification",
    )
    if any(marker in searchable for marker in access_block_markers):
        raise PipelineStop("blocked", "source_access_blocked")
    # Match explicit missing-content notices, never the generic word subscribe.
    # Whitespace folding catches provider line wrapping without changing body.
    paywall_markers = (
        "subscribe to continue reading", "subscribe to read the full article",
        "subscribe to read this article", "subscription required to read",
        "unlock the full article", "this is a preview of the article",
        "this is a preview of this article", "you are reading a preview",
        "article preview only", "preview ends here",
        "the rest of this article is for subscribers",
        "remaining content is for subscribers",
        "訂閱後繼續閱讀", "訂閱以繼續閱讀", "訂閱以閱讀全文", "訂閱後閱讀全文",
        "订阅后继续阅读", "订阅以继续阅读", "订阅以阅读全文", "订阅后阅读全文",
    )
    folded_searchable = " ".join(searchable.split())
    if any(marker in folded_searchable for marker in paywall_markers):
        raise PipelineStop("blocked", "source_access_blocked")


def parse_api_capture(provider: str, payload: dict[str, object], requested_url: str) -> UrlCapture:
    """Re-extract the full saved API response with strict single-URL binding.

    Canonical text is the entire provider Markdown, not a summary, query-ranked
    chunk, or a second pass through website-chrome truncation heuristics.
    """
    requested = normalize_capture_url(requested_url)
    if not requested or not isinstance(payload, dict):
        raise PipelineStop("blocked", "invalid_api_capture")
    if payload.get("error") or payload.get("errors"):
        raise PipelineStop("blocked", "provider_unsuccessful")
    if provider == "firecrawl":
        data = payload.get("data")
        if payload.get("success") is not True or not isinstance(data, dict):
            raise PipelineStop("blocked", "provider_unsuccessful")
        metadata = data.get("metadata")
        if not isinstance(metadata, dict):
            raise PipelineStop("blocked", "missing_response_url")
        urls = [metadata[key] for key in ("sourceURL", "url") if metadata.get(key)]
        if not urls or any(normalize_capture_url(str(value)) != requested for value in urls):
            raise PipelineStop("blocked", "response_url_mismatch")
        status = metadata.get("statusCode")
        if metadata.get("error") or data.get("error") or data.get("errors"):
            raise PipelineStop("blocked", "source_http_error")
        if status is not None:
            try:
                status_number = int(str(status))
            except ValueError:
                raise PipelineStop("blocked", "source_http_error") from None
            if not 200 <= status_number < 300:
                raise PipelineStop("blocked", "source_http_error")
        if data.get("warning"):
            raise PipelineStop("blocked", "provider_capture_warning")
        if not isinstance(data.get("markdown"), str):
            raise PipelineStop("blocked", "missing_full_markdown")
        capture = parse_firecrawl_capture(payload, requested)
    elif provider == "tavily":
        results, failed = payload.get("results"), payload.get("failed_results", [])
        if not isinstance(results, list) or not isinstance(failed, list):
            raise PipelineStop("blocked", "invalid_result_collection")
        # A single-URL request has no legitimate unrelated failure/result rows.
        if failed:
            raise PipelineStop("blocked", "provider_failed_results")
        if len(results) != 1 or not isinstance(results[0], dict):
            raise PipelineStop("blocked", "ambiguous_result_collection")
        item = results[0]
        if item.get("error") or item.get("errors"):
            raise PipelineStop("blocked", "provider_unsuccessful")
        if not item.get("url") or normalize_capture_url(str(item["url"])) != requested:
            raise PipelineStop("blocked", "response_url_mismatch")
        if not isinstance(item.get("raw_content"), str) or not item["raw_content"].strip():
            raise PipelineStop("blocked", "missing_raw_content")
        if payload.get("query") or payload.get("chunks_per_source") or item.get("chunks"):
            raise PipelineStop("blocked", "query_chunks_not_full_body")
        capture = parse_tavily_capture(payload, requested)
    else:
        raise PipelineStop("blocked", "unsupported_api_provider")
    if not isinstance(capture.markdown, str) or not capture.markdown.strip():
        raise PipelineStop("blocked", "missing_full_markdown")
    canonical = capture.markdown.replace("\r\n", "\n").replace("\r", "\n").strip()
    validate_full_capture_body(canonical, capture.title)
    declared_title = metadata.get("title") if provider == "firecrawl" else item.get("title")
    title_origin = "provider_metadata" if isinstance(declared_title, str) and declared_title.strip() else (
        "source_heading" if first_markdown_heading(canonical) else "url_fallback")
    return replace(capture, markdown=canonical,
                   metadata={**capture.metadata, "title_origin": title_origin})


def capture_url_api_first(url: str, timeout_seconds: float = 30) -> dict[str, object]:
    """One Firecrawl attempt, then one Tavily attempt; caller owns HTML fallback.

    The shared monotonic budget bounds provider timeouts and forbids later calls
    after exhaustion. Socket/DNS scheduling remains subject to the OS runtime;
    there are no retries, background requests, or hidden query/chunk extraction.
    """
    requested = normalize_capture_url(url)
    if (not requested or isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or not math.isfinite(timeout_seconds) or not 2 <= timeout_seconds <= 180):
        raise PipelineStop("blocked", "invalid_api_capture_budget_or_url")
    started = time.monotonic()
    deadline = started + timeout_seconds
    attempts: list[dict[str, object]] = []
    for index, (provider, fetch) in enumerate((("firecrawl", scrape_firecrawl), ("tavily", extract_tavily))):
        remaining = deadline - time.monotonic()
        if remaining < 1:
            attempts.append({"provider": provider, "status": "unavailable", "code": "capture_budget_exhausted"})
            continue
        allowance = max(1.0, remaining / (2 - index))
        try:
            payload = fetch(requested, timeout_seconds=allowance)
            if time.monotonic() > deadline:
                raise PipelineStop("blocked", "capture_budget_exhausted")
            capture = parse_api_capture(provider, payload, requested)
        except (PipelineStop, ValueError, OSError) as exc:
            # Never retain provider body/error text: it can echo credentials.
            safe_codes = {"missing_credentials", "http_error", "transport_error", "invalid_json",
                          "response_too_large", "provider_unsuccessful", "missing_response_url",
                          "response_url_mismatch", "source_http_error", "provider_capture_warning",
                          "invalid_result_collection", "provider_failed_results", "ambiguous_result_collection",
                          "missing_raw_content", "query_chunks_not_full_body", "missing_full_markdown",
                          "capture_budget_exhausted", "source_body_too_short", "source_body_unreadable",
                          "source_access_blocked"}
            code = exc.message if isinstance(exc, PipelineStop) and exc.message in safe_codes else "invalid_provider_response"
            attempts.append({"provider": provider, "status": "unavailable" if code == "missing_credentials" else "failed", "code": code})
            continue
        attempts.append({"provider": provider, "status": "captured", "code": "full_markdown_received"})
        return {"status": "captured", "provider": provider, "requested_url": requested,
                "response_url": capture.url, "title": capture.title, "markdown": capture.markdown,
                "title_origin": capture.metadata["title_origin"],
                "raw_response": payload, "attempts": attempts,
                "elapsed_seconds": round(time.monotonic() - started, 6)}
    return {"status": "fallback_required", "requested_url": requested, "attempts": attempts,
            "elapsed_seconds": round(time.monotonic() - started, 6)}


def api_json_request(endpoint: str, token: str, payload: dict[str, object], timeout_seconds: float) -> dict[str, object]:
    request = urllib.request.Request(endpoint, data=json.dumps(payload).encode("utf-8"),
                                    headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"}, method="POST")
    started = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            if getattr(response, "status", 200) != 200:
                raise PipelineStop("blocked", "http_error")
            chunks, count = [], 0
            while True:
                if time.monotonic() - started >= timeout_seconds:
                    raise PipelineStop("blocked", "capture_budget_exhausted")
                chunk = response.read1(65536)
                if not chunk:
                    break
                count += len(chunk)
                if count > 16 * 1024 * 1024:
                    raise PipelineStop("blocked", "response_too_large")
                chunks.append(chunk)
        value = json.loads(b"".join(chunks).decode("utf-8"))
        if not isinstance(value, dict):
            raise PipelineStop("blocked", "invalid_json")
        return value
    except urllib.error.HTTPError:
        raise PipelineStop("blocked", "http_error") from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise PipelineStop("blocked", "transport_error") from None
    except (UnicodeError, json.JSONDecodeError):
        raise PipelineStop("blocked", "invalid_json") from None


def scrape_firecrawl(url: str, *, timeout_seconds: float = 90) -> dict[str, object]:
    try:
        token = firecrawl_token()
    except PipelineStop:
        raise PipelineStop("blocked", "missing_credentials") from None
    return api_json_request(FIRECRAWL_ENDPOINT, token, {
            "url": url,
            "formats": ["markdown"],
            "onlyMainContent": True,
            "timeout": min(DEFAULT_TIMEOUT_MS, max(1000, int(timeout_seconds * 1000))),
        }, timeout_seconds)


def extract_tavily(url: str, *, timeout_seconds: float = 45) -> dict[str, object]:
    try:
        token = tavily_token()
    except PipelineStop:
        raise PipelineStop("blocked", "missing_credentials") from None
    return api_json_request(TAVILY_EXTRACT_ENDPOINT, token, {
            "urls": url,
            "include_images": True,
            "include_favicon": True,
            "format": "markdown",
            "extract_depth": "advanced",
            "timeout": min(60.0, max(1.0, timeout_seconds)),
            "include_usage": True,
        }, timeout_seconds)


def firecrawl_token() -> str:
    token = os.environ.get("FIRECRAWL_API_KEY", "").strip()
    if token:
        return token
    if os.name == "nt":
        try:
            import winreg

            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
                value, _ = winreg.QueryValueEx(key, "FIRECRAWL_API_KEY")
                token = str(value).strip()
                if token:
                    return token
        except OSError:
            pass
    raise PipelineStop("blocked", "FIRECRAWL_API_KEY is not available in process or user environment")


def tavily_token() -> str:
    token = os.environ.get("TAVILY_API_KEY", "").strip()
    if token:
        return token
    if os.name == "nt":
        try:
            import winreg

            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
                value, _ = winreg.QueryValueEx(key, "TAVILY_API_KEY")
                token = str(value).strip()
                if token:
                    return token
        except OSError:
            pass
    raise PipelineStop("blocked", "TAVILY_API_KEY is not available in process or user environment")


def render_capture_markdown(capture: UrlCapture) -> str:
    return (
        f"# Source capture: {capture.title}\n\n"
        f"- Capture provider: {capture.provider}\n"
        f"- Capture method: {capture.capture_method}\n"
        f"- Source URL: {capture.url}\n"
        f"- Status code: {capture.status_code if capture.status_code is not None else 'unknown'}\n"
        f"- Markdown length: {len(capture.markdown)}\n"
        f"- Credential reference: {capture.credential_reference} (value not stored)\n\n"
        "## Markdown\n\n"
        f"{capture.markdown.rstrip()}\n"
    )


def render_review_packet(
    source_id: str,
    title: str,
    capture: UrlCapture,
    capture_date: str,
    main_markdown: str,
    source_retention_mode: str,
    canonical_body_sha256: str,
    representative_anchors: list[str],
    source_slice: Path,
    expected_asset: Path,
    expected_assets: list[Path],
    quality: CaptureQuality,
    review_gate: dict[str, object],
    url_binding_status: str,
) -> dict[str, object]:
    headings = markdown_headings(main_markdown)[:24]
    is_user_image = capture.source_input_kind == "user_provided_image"
    image_review_actions = (
        [
            "AI 必須直接讀取保存的原圖，建立 visual analysis JSON；它必須有逐字轉寫、版面結構、IMG- 視覺錨點、主張、覆蓋表及第二輪校對證據。",
            "圖片可見內容、AI 解讀與 使用者 應用必須分開；圖片上的人物引述、數字或署名未有外部來源時，只可標為 image_visible 或 needs_external_verification。",
            "先執行 image-review；其 JSON、原圖 SHA-256、尺寸和來源 slice 視覺解讀區必須一致，才可改為 user_image_complete。",
        ]
        if is_user_image
        else []
    )
    return {
        "status": quality.status,
        "source_id": source_id,
        "title": title,
        "source_input_kind": capture.source_input_kind,
        "source_identity": source_identity(capture),
        "source_content_sha256": capture.source_content_sha256,
        "source_image_sha256": capture.source_image_sha256,
        "source_image_mime": capture.source_image_mime,
        "source_image_width": capture.source_image_width,
        "source_image_height": capture.source_image_height,
        "source_url": capture.url,
        "source_url_binding_status": url_binding_status,
        "status_code": capture.status_code,
        "capture_provider": capture.provider,
        "capture_method": capture.capture_method,
        "capture_date": capture_date,
        "markdown_length": len(capture.markdown),
        "main_markdown_length": len(main_markdown),
        "source_retention_mode": source_retention_mode,
        "infographic_generation_contract": INFOGRAPHIC_GENERATION_CONTRACT,
        "canonical_body_required": source_retention_mode == "full_body_required",
        "canonical_body_sha256": canonical_body_sha256,
        "canonical_body_representative_anchors": representative_anchors,
        "capture_quality": capture_quality_payload(quality),
        "review_gate": review_gate,
        "headings": headings,
        "long_form_segments": quality.long_form_segments,
        "infographic_planning": quality.infographic_planning,
        "main_excerpt": excerpt_text(main_markdown, limit=3600),
        "source_slice": str(source_slice),
        "expected_asset": str(expected_asset),
        "expected_assets": [str(path) for path in expected_assets],
        "required_review_actions": [
            (
                "AI 對照原始圖片確認可見內容、版面和限制，然後才可把可讀性狀態改為本來源類型允許的正式狀態。"
                if is_user_image
                else "AI 對照 source_capture.md 確認正文完整度，然後才可把可讀性狀態改為本來源類型允許的正式狀態。"
            ),
            *image_review_actions,
            "AI 先讀 capture_quality；若狀態是 blocked，不可生成 reviewed slice 或正式入庫。",
            "長文必須逐段核對 long_form_segments，確保不是只看 main_excerpt 就下結論。",
            "若使用者要求原文、全文或完整內容入庫，正式 prepare 入口必須使用 source_retention_mode=full_body_required；source slice 必須保留清理後 canonical article body、正文 SHA-256 與代表錨點。",
            "AI 把原文主張、原文證據、TL;DR、知識點與關係草案全部改成來源可驗證內容。",
            "AI 完成來源分析後，把 AI 來源分析狀態改為 complete，並寫入一句證據。",
            "AI 必須完成跨來源洞見判斷，逐項回答補底、加深、連接、修正、行動；沒有真洞見時標記 no_compounding_insight，不可硬湊。",
        "AI 必須寫入 compounding_insight_status、compounding_insight_types、compounding_insight_links 與 compounding_next_action 對應內容。",
        "AI 必須先建立重要知識覆蓋表，逐項填寫覆蓋 ID、類型、重要知識、原文證據、原文語氣、指定頁與逐字圖面表達；頁數由完整覆蓋決定。",
        "每頁必須填寫來源主題、內容用途、圖型、圖面結構、排板原型、覆蓋 ID 與忠於原文的語氣口徑檢查；來源主題必須逐字出現在標題或副標，讓圖片離開上下文仍可辨認題旨。",
        "AI 必須先逐字校對每頁標題、副標、圖內文字、數字、來源語氣及跨頁覆蓋；只有圖文鎖定稿校對狀態為 passed 並留下具體校對證據，才可派發一次 gpt-image-2。",
        "AI 必須在資訊圖鎖定稿寫明承接邏輯、連線 / 分組理由與斷橋檢查；正確片段缺承接不可入庫。",
        "AI 必須按資訊圖第 1 節防錯原則填寫本頁讀者問題、本頁一句讀後收穫、跨頁承接、圖示語義清單、重複文字 / 數字檢查及正文敘事充足性檢查。",
        "正式資訊圖只可由 gpt-image-2 經合規背景 worker 直接生成 PNG；HTML、SVG、CSS、canvas、截圖、fixture、舊圖或本地 renderer 一律不得作正式資產。",
        "每頁預設只派發一個正式 gpt-image-2 候選；只有機械阻斷失敗時才可做唯一一次針對性修復，不得為一般美化重做。",
        "gpt-image-2 回傳後只做鎖定稿／尺寸／可讀性／footer／provider-model／PNG 雜湊的機械讀回；一般人工美學批准不在正常完成關鍵路徑。",
        "AI 審稿必須拒收上一輪任務主題、非本來源實體或白名單以外文字；這項檢查留在內容 checker，不寫入圖面。",
        ],
        "stop_points": [
            "capture_quality.status 是 blocked",
            "capture_quality.issues 有未處理的 blocked 或 needs_review 問題",
            "長文分段未逐段核對",
            "source_retention_mode 是 full_body_required 但 source slice 缺少正文保存區、正文 SHA-256 或代表錨點",
            "仍是 partial_capture",
            "圖片來源未完成 image-review，或沒有逐字轉寫、視覺錨點、覆蓋表與第二輪校對",
            "AI 來源分析狀態不是 complete",
            "來源分析仍有待補",
            "跨來源洞見判斷缺少狀態、類型、扣連、下一步或五類逐項判斷",
            "資訊圖鎖定稿仍有待補",
            "重要知識覆蓋表缺原文證據、原文語氣或逐字圖面表達",
            "來源主題未逐字出現在標題或副標，內容用途／圖型／圖面結構未通過第 3 節 gate，或語氣口徑未確認忠於原文",
            "任何一頁的圖文鎖定稿校對狀態不是 passed，或沒有具體校對證據",
            "排板原型、覆蓋 ID、承接邏輯、連線 / 分組理由或斷橋檢查仍有待補",
            "本頁讀者問題、本頁一句讀後收穫、跨頁承接、圖示語義清單、重複文字 / 數字檢查或正文敘事充足性檢查仍有待補",
            "gpt-image-2 未回傳符合鎖定 prompt 的實際 PNG，或 provider/model／lock／PNG 雜湊不一致",
            "PNG 語義驗收狀態不是 passed",
            "expected_asset 不存在",
        ],
        "next_commands": [
            f"python tools\\firecrawl_obsidian_pipeline.py imagegen-action-envelope --source-slice {source_slice} --json",
            f"python tools\\firecrawl_obsidian_pipeline.py imagegen-complete --source-slice {source_slice} --response-file <gpt-image-2-response.json> --json",
            f"python tools\\firecrawl_obsidian_pipeline.py ingest --source-slice {source_slice} --json",
            f"python tools\\firecrawl_obsidian_pipeline.py ingest --source-slice {source_slice} --write --json",
        ],
    }


def capture_quality_summary(quality: CaptureQuality) -> str:
    if not quality.issues:
        return "未見機械阻塞；仍需人工／AI 審稿確認"
    issue_text = "；".join(f"{issue['severity']}:{issue['code']}" for issue in quality.issues)
    return f"{quality.status}（{issue_text}）"


def long_form_segment_hint(quality: CaptureQuality) -> str:
    if not quality.long_form_segments:
        return "未達長文分段門檻；仍需核對完整 capture"
    return f"偵測到 {len(quality.long_form_segments)} 個長文分段，審稿時必須逐段核對，不可只讀摘錄"


def infographic_planning_hint(quality: CaptureQuality) -> str:
    plan = quality.infographic_planning
    count = plan.get("recommended_count", 1)
    reasons = plan.get("reasons") if isinstance(plan.get("reasons"), list) else []
    reason_text = "；".join(str(reason) for reason in reasons[:2])
    return f"擷取階段暫估 {count} 張（只作閱讀提示，不是頁數上限）；{reason_text}；正式頁數由重要知識覆蓋表決定"


def expected_asset_paths(assets_dir: Path, source_stem: str, quality: CaptureQuality) -> list[Path]:
    # Capture length, heading count, and source-image count are hints only.
    # The reviewed knowledge coverage table decides the formal page count.
    return [assets_dir / f"{source_stem}_source.png"]


def render_infographic_draft_blocks(
    source_id: str,
    slug: str,
    title: str,
    capture_date: str,
    quality: CaptureQuality,
) -> str:
    source_stem = f"{source_id}_{slug}"
    assets = expected_asset_paths(Path("assets"), source_stem, quality)
    infographic_hint = infographic_planning_hint(quality)
    blocks: list[str] = []
    for index, asset in enumerate(assets, start=1):
        division_hint = "待 AI 完成重要知識覆蓋表後決定本頁分工；需要多少頁就建立多少頁，不得為節省生成次數壓縮。"
        blocks.append(
            f"""> PNG 尚未生成。先完成以下文字鎖定稿，再派發一次正式 gpt-image-2 生成 `{asset.as_posix()}`；HTML、SVG、CSS、本地 renderer、fixture、舊圖或人工替代一律不得作正式資產。一般人工審圖不在正常完成關鍵路徑。

### 資訊圖 {index}:待命名頁面

- 學習功能:待 AI 按原文決定。
- 原文資訊功能:待 AI 按原文決定。
- 對應閱讀障礙:待 AI 按原文決定。
- 內容用途:待補（事實真源／工作流程／學習材料／判斷材料／品味材料／行動材料／噪音暫存）
- 圖型:待補（必須是第 3 節同一內容用途列內的圖型）
- 圖面結構:待補（必須包含 visual_structure.kind 及該圖型要求的 row / step / node / item 等結構）
- 本圖分工:{division_hint}
- 本頁讀者問題:待 AI 按第 1 節防錯原則補寫。
- 本頁一句讀後收穫:待 AI 按第 1 節防錯原則補寫。
- 跨頁承接:待 AI 按第 1 節防錯原則補寫。
- 來源主題:待補（必須逐字出現在標題或副標）
- 標題:待補
- 副標:待補
- 排板原型:待補（路徑總覽／對照取捨／流程步驟／比較矩陣／時間線／概念分區）
- 覆蓋 ID:K01
- 語義分層:待補
- 重要性覆蓋:待 AI 盤點原文主要概念、流程、證據、案例、限制、數字、反方觀點與行動條件，並標明哪些進入圖面、哪些進入知識點、哪些只保留在 Markdown 真源。
- 本 URL 資訊圖數量判斷:工具初判:{infographic_hint}；AI 必須按原文重要性再判斷一張圖是否足夠，若單圖會漏掉重要內容，必須拆成多張圖並說明分工。
- 承接邏輯:待 AI 補寫圖內重點的先後、因果、對比、層級或證據承接。
- 連線 / 分組理由:待 AI 逐項說明箭咀、連線、相鄰區塊或同組項目的關係。
- 斷橋檢查:待 AI 確認沒有把各自正確但互不承接的片段硬放在一起。
- 原文實體錨點:
  - 待補
- 圖內文字:
  1. 待補
- 數字 / 標籤:待補
- 圖示語義清單:待 AI 按第 1 節防錯原則補寫；不用圖示也要寫明。
- 重複文字 / 數字檢查:待 AI 按第 1 節防錯原則補寫。
- 正文敘事充足性檢查:待 AI 按第 1 節防錯原則補寫。
- 來源註腳:Source: {title}, accessed {capture_date}.
- 語氣口徑檢查:待 AI 逐項對照原文證據，確認忠於原文事實、層級與語氣，不把 AI 綜合寫成原文立場。
- 圖文鎖定稿校對狀態:pending
- 圖文鎖定稿校對證據:待 AI 逐字核對標題、副標、圖內文字、數字、來源語氣及跨頁覆蓋，並確認圖中文字自足、短而厚實；未通過前不得生圖。
- 不可加入:來源稽核狀態、知識用途分流、使用者 應用、Obsidian / Markdown 入庫流程、本機路徑、品味入網判斷、本專案運作邏輯。
- 圖面污染檢查:待 AI 補寫。
- PNG 生成方式:待 gpt-image-2 直接生成 PNG（經合規背景 worker）。
- PNG 語義驗收狀態:pending
- PNG 語義驗收證據:待 AI 對照鎖定稿、候選 PNG 與來源內容完成語義驗收。
- PNG 反審狀態:pending
- PNG 反審證據:pending；正式通過前必須按 docs/資訊圖生成規格.md 的 INFOGRAPHIC_REVERSE_REVIEW_CONTRACT 完成 required_review_axes，並列明失敗條件或反例檢查。
- 語義驗收:待 AI 補寫。
- 驗收句:待 AI 補寫."""
        )
    coverage_table = """### 重要知識覆蓋表

> 先完整盤點 URL 的重要主張、數字、限制、比較、方法、例子與行動，再按不可合併的理解主題決定頁數。每項只指定一頁；頁數由完整覆蓋決定，不由固定上限或來源篇幅決定。

| 覆蓋 ID | 類型 | 重要知識 | 原文證據 | 原文語氣 | 指定頁 | 圖面表達 |
|---|---|---|---|---|---:|---|
| K01 | 待補 | 待 AI 完整盤點 | 待補 | 待補 | 1 | 待補 |
"""
    return coverage_table + "\n" + "\n\n".join(blocks)


def render_slice_draft(
    source_id: str,
    slug: str,
    title: str,
    capture: UrlCapture,
    capture_date: str,
    source_type: str,
    knowledge_use: str,
    main_markdown: str,
    source_retention_mode: str,
    canonical_body_sha256: str,
    representative_anchors: list[str],
    quality: CaptureQuality,
    url_binding_status: str,
) -> str:
    outline = "\n".join(f"- {heading}" for heading in markdown_headings(main_markdown)[:20]) or "- 未偵測到 Markdown 標題"
    excerpt = excerpt_text(main_markdown)
    quality_summary = capture_quality_summary(quality)
    segment_hint = long_form_segment_hint(quality)
    infographic_hint = infographic_planning_hint(quality)
    is_user_text = capture.source_input_kind == "user_provided_text"
    is_user_image = capture.source_input_kind == "user_provided_image"
    source_image_asset = (
        f"assets/{source_id}_{slug}_original{capture.source_image_extension}"
        if is_user_image
        else ""
    )
    source_identity_rows = (
        f"| 來源輸入類型 | user_provided_text |\n"
        f"| 來源身分 | {source_identity(capture)} |\n"
        f"| 來源內容 SHA-256 | {capture.source_content_sha256} |\n"
        if is_user_text
        else (
            (
                f"| 來源輸入類型 | user_provided_image |\n"
                f"| 來源身分 | {source_identity(capture)} |\n"
                f"| 原始圖片檔 | {source_image_asset} |\n"
                f"| 來源圖片 SHA-256 | {capture.source_image_sha256} |\n"
                f"| 圖片 MIME | {capture.source_image_mime} |\n"
                f"| 圖片尺寸 | {capture.source_image_width} × {capture.source_image_height} |\n"
            )
            if is_user_image
            else (
                f"| 來源輸入類型 | url |\n"
                f"| 來源身分 | {source_identity(capture)} |\n"
                f"| URL | {capture.url} |\n"
                f"| URL 綁定狀態 | {url_binding_status} |\n"
            )
        )
    )
    author_row = (
        "使用者提供 Markdown；作者與發布日期只可按正文明示內容補寫，未標明則保留未標明"
        if is_user_text
        else (
            "使用者提供圖片；圖片內署名或來源只可標為圖片可見內容，未有原始 URL 或可核對出處前不得升格為外部已核實事實"
            if is_user_image
            else "待 AI 從 source capture 補寫；原文沒有則寫未標明"
        )
    )
    readability_status = (
        "user_text_pending_review" if is_user_text
        else ("user_image_pending_review" if is_user_image else "partial_capture")
    )
    ready_status = (
        "user_text_complete" if is_user_text
        else ("user_image_complete" if is_user_image else "public_ok")
    )
    completion = (
        f"使用者提供文字檔已逐位元組保存；內容 SHA-256 為 `{capture.source_content_sha256}`；仍需 AI 對照 capture 確認正文與來源稽核，確認後才可改為 `user_text_complete`"
        if is_user_text
        else (
            f"原始圖片已逐位元組保存；圖片 SHA-256 為 `{capture.source_image_sha256}`；仍需 AI 建立完整視覺解讀、錨點、覆蓋表與第二輪校對，確認後才可改為 `user_image_complete`"
            if is_user_image
            else f"{capture.provider} 已回傳 Markdown {len(capture.markdown)} 字元；審核包正文摘錄 {len(main_markdown)} 字元；擷取品質:{quality_summary}；仍需 AI 對照 capture 確認正文是否完整，確認後才可改為 `public_ok`"
        )
    )
    representative_anchor_text = " / ".join(representative_anchors) if representative_anchors else "待補"
    body_section = ""
    if source_retention_mode == "full_body_required":
        body_fence = markdown_fence_for(main_markdown)
        body_section = f"""
## 正文保存區

| 欄位 | 內容 |
|---|---|
| 保存模式 | full_body_required |
| 正文 SHA-256 | {canonical_body_sha256} |
| 正文來源 | source_capture.md 經 main_content_markdown 清理後的 canonical article body |
| 正文清理邊界 | 排除網站導覽、留言、彈窗、上一篇/下一篇、搜尋與商品提示等非正文內容 |
| 代表錨點 | {representative_anchor_text} |

### 正文代表錨點

{chr(10).join(f"- {anchor}" for anchor in representative_anchors) if representative_anchors else "- 待 AI 補代表錨點"}

### Canonical article body

{body_fence}markdown
{main_markdown.rstrip()}
{body_fence}
"""
    return f"""# {source_id} — {title}

## 來源稽核

| 欄位 | 內容 |
|---|---|
{source_identity_rows}
| 來源名稱 | {title} |
| 來源類型 | {source_type} |
| 作者 / 發布日期 | {author_row} |
| 擷取日期 | {capture_date} |
| 抓取方式 | {capture.capture_method} |
| 可讀性狀態 | {readability_status} |
| 正文保存模式 | {source_retention_mode} |
| 正文保存 SHA-256 | {canonical_body_sha256} |
| 正文代表錨點 | {representative_anchor_text} |
| 完整度 | {completion} |
| 是否可入庫 | 不可以；需由 AI 完成來源分析、把可讀性狀態改為 `{ready_status}`、把 AI 來源分析狀態改為 `complete` 後才可入庫 |
| vault_write_status | not_written |
| AI 來源分析狀態 | pending |
| AI 來源分析證據 | 待 AI 對照 capture 完成來源稽核、原文主張、證據、摘要、知識點與關係。 |

{body_section}
{render_image_analysis_draft(capture, source_image_asset)}
## 原文層

> 狀態:{capture.provider} 擷取已完成；本節仍是工作草稿，未經 AI 整理前不得直接寫入 Obsidian vault。
> 長文核對:{segment_hint}

### Capture 偵測標題

{outline}

### Capture 原文摘錄

```text
{excerpt}
```

### 原文主張

1. 待 AI 按 `source_capture.md` 補寫。

### 原文例子 / 人物 / 數據

| 類型 | 原文內容 |
|---|---|
| 待補 | 待 AI 按 `source_capture.md` 補寫 |

### 原文證據

待 AI 按 `source_capture.md` 補寫。

## 一句定位

待 AI 按原文補寫一句定位。

## TL;DR

1. 待 AI 按原文補寫。

## 關鍵知識點

| ID | 知識點 | 層級 | 可信度 | 來源證據 |
|---|---|---|---|---|
| KP-{slug.upper()[:8]}-001 | 待 AI 補寫原子知識點。 | 原文主張 | 中 | 待補 |

## 新手入口

| 概念 | 入門解釋 |
|---|---|
| 待補 | 待 AI 補寫 |

## 概念拆解

### 日常類比

待 AI 補寫。

### 正式定義

待 AI 補寫。

### 適用邊界

1. 待 AI 補寫。

## 知識用途分流

| 欄位 | 判斷 |
|---|---|
| 主要用途 | {knowledge_use} |
| 是否入事實知識網 | 待判斷 |
| 入事實網原因 | 待 AI 補寫 |
| 是否入品味探索網 | 待判斷 |
| 不入或入品味網原因 | 待 AI 補寫 |
| 即時實用產出 | 待 AI 補寫 |
| 第二階段訊號 | 待 AI 補寫 |

## For 使用者:個人化消化

### 關係

待 AI 補寫；本節只會進 application note，不可進 source note。

### 既有知識扣連

1. 待 AI 補寫。

### 採納判斷

- 狀態:待 AI 補寫
- 一句原因:待 AI 補寫。

### 品味扣連

- 狀態:待 AI 補寫
- 一句原因:待 AI 補寫；若來源不屬品味 / 生活 / 選擇材料，寫 `not_applicable` 並說明原因。

### 跨來源洞見判斷

- 狀態:needs_context
- 類型:
- 扣連:
- 下一步:待 AI 判斷：指出仍需比對的既有材料或可驗證下一步。
- 補底:待 AI 判斷；需比對實際既有來源、知識點或項目錨點。
- 加深:待 AI 判斷；需比對實際既有來源、知識點或項目錨點。
- 連接:待 AI 判斷；需比對實際既有來源、知識點或項目錨點。
- 修正:待 AI 判斷；需比對實際既有來源、知識點或項目錨點。
- 行動:待 AI 判斷；需比對實際既有來源、知識點或項目錨點。

### 應用場景

1. 待 AI 補寫。

## 資訊圖

{render_infographic_draft_blocks(source_id, slug, title, capture_date, quality)}

## 原子知識點與關係

### 知識點草案

| 知識點 ID | 知識點 | 層級 | 可信度 | 來源證據 |
|---|---|---|---|---|
| KP-{slug.upper()[:8]}-001 | 待 AI 補寫。 | 原文主張 | 中 | 待補 |

### 關係草案

| 來源知識點 | 目標知識點 | 關係 | 證據錨點 | 信心 |
|---|---|---|---|---|

## 學習檢查

| 題型 | 問題 | 答案標準 |
|---|---|---|
| 理解題 | 待 AI 補寫。 | 待 AI 補寫。 |

## Obsidian-ready 索引欄位

> 註:本節標題沿用 adapter 現有解析標記，實際內容必須維持 Obsidian-first；完成審核前不得正式入庫。
"""


def render_image_analysis_draft(capture: UrlCapture, source_image_asset: str) -> str:
    if capture.source_input_kind != "user_provided_image":
        return ""
    return f"""## 視覺解讀與校對

<!-- IMAGE_ANALYSIS_START -->
> 原圖：{source_image_asset}；SHA-256：{capture.source_image_sha256}；尺寸：{capture.source_image_width} × {capture.source_image_height}。
> 必須先以 image-review 寫入完整解讀。不可只做 OCR；必須有逐字轉寫、版面結構、圖片錨點、來源主張、完整覆蓋表與第二輪校對。

### 圖片結構

待 AI 讀取原圖後補寫。

### 圖中文字逐字轉寫

待 AI 讀取原圖後補寫；看不清處必須明示。

### 視覺證據錨點

| 錨點 | 類型 | 區域 | 可見證據 |
|---|---|---|---|
| 待補 | 待補 | x/y/width/height | 待 AI 補寫 |

### 圖片來源主張

待 AI 補寫；未有外部出處時，必須標示為圖片可見內容。

### 視覺知識覆蓋表

| 圖片錨點 | 去向 | 理由 |
|---|---|---|
| 待補 | 待補 | 待 AI 補寫 |

### 第二輪校對

- 狀態:pending
- 證據:待 AI 重新對照原圖，確認沒有漏掉重要文字、數字、比較條件、箭咀關係、註腳或限制。
<!-- IMAGE_ANALYSIS_END -->

"""


def image_review_command(root: Path, args: argparse.Namespace) -> int:
    source_slice = resolve_inside(root, args.source_slice)
    analysis_file = resolve_inside(root, args.analysis_file)
    if not source_slice.exists() or not analysis_file.exists():
        raise PipelineStop("blocked", "image-review requires an existing source slice and visual analysis JSON")
    metadata_path = source_slice.parent / "source_capture_metadata.json"
    review_packet_path = source_slice.parent / "review_packet.json"
    if not metadata_path.exists() or not review_packet_path.exists():
        raise PipelineStop("blocked", "image-review requires source_capture_metadata.json and review_packet.json from prepare")
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8-sig"))
        review_packet = json.loads(review_packet_path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PipelineStop("blocked", f"image-review packet is unreadable: {exc}") from exc
    if not isinstance(metadata, dict) or metadata.get("source_input_kind") != "user_provided_image":
        raise PipelineStop("blocked", "image-review is only valid for a prepared user_provided_image source")
    if not isinstance(review_packet, dict):
        raise PipelineStop("blocked", "image-review requires an object review packet")
    try:
        analysis = analysis_from_file(analysis_file)
    except ImageSourceContractError as exc:
        raise PipelineStop("blocked", str(exc)) from exc
    expected_hash = str(metadata.get("source_image_sha256") or "")
    expected_width = int(metadata.get("source_image_width") or 0)
    expected_height = int(metadata.get("source_image_height") or 0)
    issues = validate_visual_analysis(
        analysis,
        expected_image_sha256=expected_hash,
        expected_width=expected_width,
        expected_height=expected_height,
    )
    if issues:
        print_payload(
            {
                "status": "needs_review",
                "message": "visual analysis did not pass the complete image-source contract",
                "issues": issues,
                "write_performed": False,
            },
            args.json,
        )
        return 1
    analysis_sha256 = canonical_analysis_sha256(analysis)
    persisted_analysis = source_slice.parent / "image_analysis.json"
    atomic_write_json(persisted_analysis, analysis)
    review_packet["image_analysis_file"] = str(persisted_analysis.relative_to(root))
    review_packet["image_analysis_sha256"] = analysis_sha256
    review_packet["image_analysis_status"] = "complete"
    review_packet["image_analysis_checker_status"] = "passed"
    atomic_write_json(review_packet_path, review_packet)
    slice_text = source_slice.read_text(encoding="utf-8-sig")
    updated_slice = replace_image_analysis_section(slice_text, analysis, analysis_sha256)
    source_slice.write_text(updated_slice, encoding="utf-8")
    print_payload(
        {
            "status": "needs_review",
            "message": "visual analysis is persisted and bound to the original image; complete the remaining reviewed source slice before ingest",
            "source_slice": str(source_slice.relative_to(root)),
            "image_analysis": str(persisted_analysis.relative_to(root)),
            "image_analysis_sha256": analysis_sha256,
            "write_performed": True,
        },
        args.json,
    )
    return 0


def atomic_write_json(path: Path, value: object) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def replace_image_analysis_section(text: str, analysis: dict[str, object], analysis_sha256: str) -> str:
    replacement = render_image_analysis_record(analysis, analysis_sha256)
    pattern = r"## 視覺解讀與校對\n\n<!-- IMAGE_ANALYSIS_START -->[\s\S]*?<!-- IMAGE_ANALYSIS_END -->\n"
    if not re.search(pattern, text):
        raise PipelineStop("blocked", "prepared image slice has no replaceable 視覺解讀與校對 section")
    return re.sub(pattern, replacement, text, count=1)


def render_image_analysis_record(analysis: dict[str, object], analysis_sha256: str) -> str:
    anchors = analysis.get("anchors")
    transcription = analysis.get("transcription")
    claims = analysis.get("source_claims")
    coverage = analysis.get("coverage")
    assert isinstance(anchors, list)
    assert isinstance(transcription, list)
    assert isinstance(claims, list)
    assert isinstance(coverage, list)
    anchor_rows = "\n".join(
        f"| {row['id']} | {row['kind']} | {format_image_region(row['region'])} | {row['evidence']} |"
        for row in anchors
        if isinstance(row, dict)
    )
    transcript_rows = "\n".join(
        f"- {row['anchor_id']}（{row['confidence']}）：{row['text']}"
        for row in transcription
        if isinstance(row, dict)
    )
    claim_rows = "\n".join(
        f"- {row['claim']}（錨點：{', '.join(str(item) for item in row['anchor_ids'])}；驗證：{row['verification_status']}）"
        for row in claims
        if isinstance(row, dict)
    )
    coverage_rows = "\n".join(
        f"| {row['anchor_id']} | {row['disposition']} | {row['reason']} |"
        for row in coverage
        if isinstance(row, dict)
    )
    dimensions = analysis["image_dimensions"]
    assert isinstance(dimensions, dict)
    return f"""## 視覺解讀與校對

<!-- IMAGE_ANALYSIS_START -->
| 欄位 | 內容 |
|---|---|
| 解讀狀態 | complete |
| 解讀 SHA-256 | {analysis_sha256} |
| 圖片類型 | {analysis['visual_type']} |
| 圖片尺寸 | {dimensions['width']} × {dimensions['height']} |
| 第二輪校對 | passed |
| 校對證據 | {analysis['checker_evidence']} |

### 圖片結構

{analysis['summary']}

### 圖中文字逐字轉寫

{transcript_rows}

### 視覺證據錨點

| 錨點 | 類型 | 區域 | 可見證據 |
|---|---|---|---|
{anchor_rows}

### 圖片來源主張

{claim_rows}

### 視覺知識覆蓋表

| 圖片錨點 | 去向 | 理由 |
|---|---|---|
{coverage_rows}

### 第二輪校對

- 狀態:passed
- 證據:{analysis['checker_evidence']}
<!-- IMAGE_ANALYSIS_END -->

"""


def format_image_region(region: object) -> str:
    if not isinstance(region, dict):
        return "invalid"
    return "/".join(f"{key}={region.get(key)}" for key in ("x", "y", "width", "height"))


def extract_infographic_blocks(text: str) -> list[str]:
    section_match = re.search(r"\n## 資訊圖\n([\s\S]*?)(?=\n##\s+|\Z)", text)
    section = section_match.group(1) if section_match else text
    matches = re.finditer(r"### 資訊圖\s+\d+:[\s\S]*?(?=\n### 資訊圖\s+\d+:|\n##\s+|\Z)", section)
    return [match.group(0).strip() for match in matches]


def extract_infographic_block(text: str) -> str:
    blocks = extract_infographic_blocks(text)
    return blocks[0] if blocks else ""


def expected_prompt_asset_paths(source_slice: Path, count: int) -> list[Path]:
    assets_dir = source_slice.parent / "assets"
    if count <= 1:
        return [assets_dir / f"{source_slice.stem}_source.png"]
    return [assets_dir / f"{source_slice.stem}_source_{index:02d}.png" for index in range(1, count + 1)]


def infographic_long_field(block: str, label: str) -> str:
    match = re.search(rf"^- {re.escape(label)}:(.*?)(?=^- [^\s]|\Z)", block, flags=re.MULTILINE | re.DOTALL)
    return match.group(1).strip() if match else ""


def infographic_list_field(block: str, label: str) -> list[str]:
    value = infographic_long_field(block, label)
    items: list[str] = []
    for line in value.splitlines():
        cleaned = re.sub(r"^\s*(?:[-*]|\d+\.)\s*", "", line).strip()
        if cleaned:
            items.append(cleaned)
    return items


def extract_infographic_coverage_rows(text: str) -> list[dict[str, object]]:
    match = re.search(r"\n### 重要知識覆蓋表\n([\s\S]*?)(?=\n### 資訊圖\s+\d+:|\n##\s+|\Z)", text)
    if not match:
        return []
    rows: list[dict[str, object]] = []
    for line in match.group(1).splitlines():
        if not line.strip().startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if cells[0] in {"覆蓋 ID", "---"} or set(cells[0]) == {"-"}:
            continue
        if len(cells) == 7:
            coverage_id, row_type, knowledge, evidence, source_tone, page_cell, visible_text = cells
        elif len(cells) == 5:
            # Legacy rows remain readable so preflight can report the missing
            # source-grounding fields instead of silently treating the table as absent.
            coverage_id, row_type, knowledge, page_cell, visible_text = cells
            evidence = ""
            source_tone = ""
        else:
            continue
        try:
            page = int(page_cell)
        except ValueError:
            page = 0
        rows.append({
            "id": coverage_id,
            "type": row_type,
            "knowledge": knowledge,
            "evidence": evidence,
            "source_tone": source_tone,
            "page": page,
            "visible_text": visible_text,
        })
    return rows


def normalized_infographic_text(value: str) -> str:
    return re.sub(r"[^\w\u4e00-\u9fff]+", "", value.casefold())


def repeated_infographic_visible_tokens(lines: list[str]) -> list[str]:
    joined = " ".join(line for line in lines if line)
    token_patterns = (
        r"\b\d+(?:\.\d+)+(?:\+)?\b",
        r"\b\d+(?:\.\d+)?\s*(?:MiB|MB|GB|KB|%)\b",
        r"\b\d{4}-\d{2}-\d{2}\b",
    )
    tokens: list[str] = []
    for pattern in token_patterns:
        tokens.extend(match.group(0).strip() for match in re.finditer(pattern, joined, flags=re.IGNORECASE))
    for line in lines:
        normalized = normalized_infographic_text(line)
        if len(normalized) >= 8:
            tokens.append(line.strip())
    repeated: list[str] = []
    comparable = [normalized_infographic_text(token) for token in tokens if token]
    for token in tokens:
        key = normalized_infographic_text(token)
        if key and comparable.count(key) > 1 and token not in repeated:
            repeated.append(token)
    return repeated


def has_page_number_leak(lines: list[str]) -> bool:
    return any(re.search(r"\b\d+\s*/\s*\d+\b", line) for line in lines)


def markdown_heading_section(text: str, heading: str, level: int = 2) -> str:
    marker = "#" * level
    match = re.search(
        rf"(?ms)^{re.escape(marker)}\s+{re.escape(heading)}\s*\n(.*?)(?=^{'#' * level}\s+|\Z)",
        text,
    )
    return match.group(1).strip() if match else ""


def canonical_source_body(text: str) -> str:
    body_section = markdown_heading_section(text, "正文保存區")
    if not body_section:
        return ""
    match = re.search(
        r"(?ms)^### Canonical article body\s*\n(?P<fence>`{3,})(?:markdown|md|text)?\s*\n(.*?)\n(?P=fence)",
        body_section,
    )
    return match.group(2).strip() if match else ""


def infographic_source_truth(text: str) -> str:
    """Return source-owned content while excluding analysis and project workflow sections."""

    canonical = canonical_source_body(text)
    if canonical:
        return canonical
    original_layer = markdown_heading_section(text, "原文層")
    if original_layer:
        return original_layer
    visual_review = markdown_heading_section(text, "視覺解讀與校對")
    if visual_review:
        source_sections = [
            markdown_heading_section(visual_review, heading, level=3)
            for heading in ("圖片結構", "圖中文字逐字轉寫", "視覺證據錨點", "圖片來源主張")
        ]
        return "\n".join(section for section in source_sections if section).strip()
    return ""


def source_topic_is_specific(value: str) -> bool:
    key = normalized_infographic_text(value)
    generic = {"ai", "openai", "anthropic", "文章", "來源", "官方", "指南", "報告", "研究", "新聞"}
    return len(key) >= 4 and key not in generic


def coverage_ids_for_block(block: str) -> list[str]:
    value = infographic_field(block, "覆蓋 ID")
    return [item.strip() for item in re.split(r"[,，、\s]+", value) if item.strip()]


def infographic_graph_structure_missing_tokens(graph_type: str, graph_structure: str) -> list[str]:
    contract = INFOGRAPHIC_GRAPH_TYPE_CONTRACTS.get(graph_type)
    if not contract:
        return []
    required_tokens = [str(contract["kind"])]
    required_tokens.extend(str(field) for field in contract.get("required_text_fields", ()))
    for collection_name, collection_contract in contract.get("collections", {}).items():
        required_tokens.append(str(collection_name))
        required_tokens.extend(str(field) for field in collection_contract.get("required_fields", ()))
    normalized_structure = normalized_infographic_text(graph_structure)
    return [
        token
        for token in required_tokens
        if normalized_infographic_text(token) not in normalized_structure
    ]


def pending_infographic_value(value: str) -> bool:
    normalized = value.strip().casefold()
    return not normalized or any(marker in normalized for marker in ("待補", "待 ai", "pending", "needs_review", "未完成"))


def infographic_preflight(text: str, blocks: list[str]) -> dict[str, object]:
    rows = extract_infographic_coverage_rows(text)
    issues: list[dict[str, object]] = []
    source_truth = infographic_source_truth(text)
    if not source_truth:
        issues.append({
            "code": "missing_infographic_source_truth",
            "message": "找不到可供第三方閱讀成果使用的原始來源內容；不可生成。",
        })
    if not rows:
        issues.append({"code": "missing_coverage_table", "message": "缺少重要知識覆蓋表；不可生成。"})
    ids = [str(row["id"]) for row in rows]
    duplicate_ids = sorted({item for item in ids if ids.count(item) > 1})
    if duplicate_ids:
        issues.append({"code": "duplicate_coverage_ids", "ids": duplicate_ids})
    page_assignments: dict[str, list[int]] = {item: [] for item in ids}
    visible_lines_by_page: dict[int, list[str]] = {}
    source_topics: list[str] = []
    page_summaries: list[dict[str, object]] = []
    requires_ai_headline = source_title_for_infographic(text) is None
    for index, block in enumerate(blocks, start=1):
        source_topic = infographic_field(block, "來源主題")
        title = infographic_field(block, "標題")
        subtitle = infographic_field(block, "副標")
        source_fidelity = infographic_field(block, "語氣口徑檢查")
        content_boundary_status = infographic_field(block, "圖面污染檢查")
        text_review_status = infographic_field(block, "圖文鎖定稿校對狀態")
        text_review_evidence = infographic_field(block, "圖文鎖定稿校對證據")
        content_use = infographic_field(block, "內容用途")
        graph_type = infographic_field(block, "圖型")
        graph_structure = infographic_field(block, "圖面結構")
        archetype = infographic_field(block, "排板原型")
        reader_context = {
            field: infographic_field(block, field)
            for field in INFOGRAPHIC_READER_CONTEXT_FIELDS
        }
        coverage_ids = coverage_ids_for_block(block)
        text_lines = infographic_list_field(block, "圖內文字")
        anchors = infographic_list_field(block, "原文實體錨點")
        numbers = infographic_field(block, "數字 / 標籤")
        required = {
            "來源主題": source_topic,
            "標題": title,
            "副標": subtitle,
            "內容用途": content_use,
            "圖型": graph_type,
            "圖面結構": graph_structure,
            "排板原型": archetype,
            "語義分層": infographic_field(block, "語義分層"),
            "承接邏輯": infographic_field(block, "承接邏輯"),
            "連線 / 分組理由": infographic_field(block, "連線 / 分組理由"),
            "斷橋檢查": infographic_field(block, "斷橋檢查"),
            "數字 / 標籤": numbers,
            "覆蓋 ID": infographic_field(block, "覆蓋 ID"),
            "語氣口徑檢查": source_fidelity,
            "圖面污染檢查": content_boundary_status,
            "圖文鎖定稿校對狀態": text_review_status,
            "圖文鎖定稿校對證據": text_review_evidence,
            **reader_context,
        }
        for label, value in required.items():
            if pending_infographic_value(value):
                issues.append({"code": "incomplete_page_field", "page": index, "field": label})
        if content_use and not pending_infographic_value(content_use):
            allowed_graph_types = INFOGRAPHIC_CONTENT_USE_GRAPH_TYPES.get(content_use)
            if allowed_graph_types is None:
                issues.append({"code": "invalid_content_use", "page": index, "value": content_use})
            elif graph_type and not pending_infographic_value(graph_type):
                if graph_type not in INFOGRAPHIC_GRAPH_TYPE_CONTRACTS:
                    issues.append({"code": "invalid_graph_type", "page": index, "value": graph_type})
                elif graph_type not in allowed_graph_types:
                    issues.append({
                        "code": "graph_type_not_allowed_for_content_use",
                        "page": index,
                        "content_use": content_use,
                        "graph_type": graph_type,
                        "allowed": list(allowed_graph_types),
                    })
                elif graph_structure and not pending_infographic_value(graph_structure):
                    missing_tokens = infographic_graph_structure_missing_tokens(graph_type, graph_structure)
                    if missing_tokens:
                        issues.append({
                            "code": "invalid_visual_structure",
                            "page": index,
                            "graph_type": graph_type,
                            "missing_tokens": missing_tokens,
                        })
        if archetype and archetype not in INFOGRAPHIC_LAYOUT_ARCHETYPES:
            issues.append({"code": "invalid_layout_archetype", "page": index, "value": archetype})
        if source_topic and not pending_infographic_value(source_topic):
            source_topics.append(normalized_infographic_text(source_topic))
            if not source_topic_is_specific(source_topic):
                issues.append({"code": "source_topic_too_generic", "page": index, "value": source_topic})
            title_band = normalized_infographic_text(f"{title} {subtitle}")
            if normalized_infographic_text(source_topic) not in title_band:
                issues.append({"code": "source_topic_not_visible_in_title_or_subtitle", "page": index, "source_topic": source_topic})
            if requires_ai_headline and normalized_infographic_text(source_topic) not in normalized_infographic_text(title):
                issues.append({"code": "ai_headline_not_source_specific", "page": index, "source_topic": source_topic, "title": title})
        if source_fidelity and not pending_infographic_value(source_fidelity) and "忠於原文" not in source_fidelity:
            issues.append({"code": "source_fidelity_not_confirmed", "page": index})
        if content_boundary_status != "passed":
            issues.append({"code": "standalone_content_boundary_not_passed", "page": index, "value": content_boundary_status})
        if text_review_status != "passed":
            issues.append({"code": "text_lock_review_not_passed", "page": index, "value": text_review_status})
        if pending_infographic_value(text_review_evidence) or len(normalized_infographic_text(text_review_evidence)) < 12:
            issues.append({"code": "text_lock_review_evidence_missing", "page": index})
        if not text_lines or any(pending_infographic_value(line) for line in text_lines):
            issues.append({"code": "incomplete_visible_text", "page": index})
        for text_quality_error in visible_text_quality_errors([
            *text_lines,
            *([numbers] if numbers else []),
        ]):
            issues.append({
                "code": "thin_visible_text",
                "page": index,
                "message": text_quality_error,
            })
        if (
            text_review_status == "passed"
            and (
                "自足" not in text_review_evidence
                or "厚實" not in text_review_evidence
            )
        ):
            issues.append({
                "code": "text_quality_review_missing",
                "page": index,
                "message": f"圖文鎖定稿校對證據必須明示已檢查{INFOGRAPHIC_TEXT_QUALITY_MARKER}",
            })
        if not anchors or any(pending_infographic_value(line) for line in anchors):
            issues.append({"code": "incomplete_source_anchors", "page": index})
        visible_text = " ".join([title, subtitle, *text_lines, numbers])
        visible_components = [title, subtitle, *text_lines, numbers]
        repeat_check = reader_context["重複文字 / 數字檢查"]
        repeated_tokens = repeated_infographic_visible_tokens(visible_components)
        if repeated_tokens and (
            pending_infographic_value(repeat_check)
            or "必要" not in repeat_check
            or not any(normalized_infographic_text(token) in normalized_infographic_text(repeat_check) for token in repeated_tokens)
        ):
            issues.append({"code": "unexplained_repeated_visible_text_or_number", "page": index, "tokens": repeated_tokens})
        if has_page_number_leak([title, subtitle, *text_lines, numbers]):
            issues.append({"code": "page_number_leaked_into_visible_content", "page": index})
        if len(text_lines) > INFOGRAPHIC_VISIBLE_ITEM_MAX:
            issues.append({"code": "visible_item_overload", "page": index, "count": len(text_lines), "max": INFOGRAPHIC_VISIBLE_ITEM_MAX})
        if len(visible_text) > INFOGRAPHIC_VISIBLE_TEXT_MAX_CHARS:
            issues.append({"code": "visible_text_overload", "page": index, "count": len(visible_text), "max": INFOGRAPHIC_VISIBLE_TEXT_MAX_CHARS})
        unknown_ids = sorted(set(coverage_ids) - set(ids))
        if unknown_ids:
            issues.append({"code": "unknown_page_coverage_ids", "page": index, "ids": unknown_ids})
        for coverage_id in coverage_ids:
            if coverage_id in page_assignments:
                page_assignments[coverage_id].append(index)
        visible_lines_by_page[index] = [title, subtitle, *text_lines, numbers]
        page_summaries.append({
            "page": index,
            "source_topic": source_topic,
            "title": title,
            "content_use": content_use,
            "graph_type": graph_type,
            "layout_archetype": archetype,
            "coverage_ids": coverage_ids,
            "visible_item_count": len(text_lines),
            "visible_text_chars": len(visible_text),
        })
    for row in rows:
        coverage_id = str(row["id"])
        row_type = str(row["type"])
        evidence = str(row.get("evidence", ""))
        source_tone = str(row.get("source_tone", ""))
        assigned_page = int(row["page"])
        if row_type not in INFOGRAPHIC_COVERAGE_TYPES:
            issues.append({"code": "invalid_coverage_type", "id": coverage_id, "value": row_type})
        if pending_infographic_value(str(row["knowledge"])) or pending_infographic_value(str(row["visible_text"])):
            issues.append({"code": "incomplete_coverage_row", "id": coverage_id})
        if pending_infographic_value(evidence) or len(normalized_infographic_text(evidence)) < 4:
            issues.append({"code": "incomplete_coverage_evidence", "id": coverage_id})
        elif normalized_infographic_text(evidence) not in normalized_infographic_text(source_truth):
            issues.append({"code": "coverage_evidence_not_found_in_source_truth", "id": coverage_id, "evidence": evidence})
        if source_tone not in INFOGRAPHIC_SOURCE_TONE_TYPES:
            issues.append({"code": "invalid_source_tone", "id": coverage_id, "value": source_tone})
        if assigned_page < 1 or assigned_page > len(blocks):
            issues.append({"code": "invalid_coverage_page", "id": coverage_id, "page": assigned_page})
            continue
        mapped_pages = page_assignments.get(coverage_id, [])
        if mapped_pages != [assigned_page]:
            issues.append({"code": "coverage_assignment_mismatch", "id": coverage_id, "table_page": assigned_page, "block_pages": mapped_pages})
        visible_key = re.sub(r"\s+", "", str(row["visible_text"]).casefold())
        page_visible = re.sub(r"\s+", "", " ".join(visible_lines_by_page.get(assigned_page, [])).casefold())
        if visible_key and visible_key not in page_visible:
            issues.append({"code": "coverage_not_visible", "id": coverage_id, "page": assigned_page, "expected": row["visible_text"]})
    coverage_visible_by_page: dict[int, list[str]] = {}
    for row in rows:
        page = int(row.get("page") or 0)
        coverage_visible_by_page.setdefault(page, []).append(str(row.get("visible_text") or ""))
    for page, lines in visible_lines_by_page.items():
        mapped_text = [
            normalized_infographic_text(value)
            for value in coverage_visible_by_page.get(page, [])
            if normalized_infographic_text(value)
        ]
        block = blocks[page - 1] if 0 < page <= len(blocks) else ""
        body_lines = infographic_list_field(block, "圖內文字")
        for line in body_lines:
            line_key = normalized_infographic_text(line)
            if line_key and not any(value in line_key or line_key in value for value in mapped_text):
                issues.append({"code": "visible_text_not_mapped_to_source_coverage", "page": page, "text": line})
    seen_lines: dict[str, int] = {}
    for page, lines in visible_lines_by_page.items():
        for line in lines:
            key = re.sub(r"[^\w\u4e00-\u9fff]+", "", line.casefold())
            if len(key) < 8:
                continue
            if key in seen_lines:
                issues.append({"code": "duplicate_visible_text_across_pages", "page": page, "first_page": seen_lines[key], "text": line})
            else:
                seen_lines[key] = page
    unassigned = sorted(item for item, pages in page_assignments.items() if not pages)
    multiply_assigned = sorted(item for item, pages in page_assignments.items() if len(pages) > 1)
    if len(set(item for item in source_topics if item)) > 1:
        issues.append({"code": "inconsistent_source_topic_across_pages", "source_topics": source_topics})
    return {
        "status": "ready" if not issues else "needs_review",
        "coverage_count": len(rows),
        "page_count": len(blocks),
        "unassigned_coverage_ids": unassigned,
        "multiply_assigned_coverage_ids": multiply_assigned,
        "pages": page_summaries,
        "issues": issues,
    }


def render_imagegen_prompt(
    *,
    source_title: str,
    source_label: str,
    original_date: str | None,
    block: str,
    index: int,
    total: int,
    generated_on: str,
    primary_work_title: str | None = None,
    primary_work_status: str = "none",
) -> str:
    source_topic = infographic_field(block, "來源主題")
    lock_title = infographic_field(block, "標題")
    subtitle = infographic_field(block, "副標")
    archetype = infographic_field(block, "排板原型")
    anchors = infographic_list_field(block, "原文實體錨點")
    visible_lines = infographic_list_field(block, "圖內文字")
    numbers = infographic_field(block, "數字 / 標籤")
    return render_gpt_image_2_prompt(
        source_title=source_title,
        source_label=source_label,
        original_date=original_date,
        source_topic=source_topic,
        lock_title=lock_title,
        subtitle=subtitle,
        archetype=archetype,
        anchors=anchors,
        visible_lines=visible_lines,
        numbers=numbers,
        index=index,
        total=total,
        generated_on=generated_on,
        primary_work_title=primary_work_title,
        primary_work_status=primary_work_status,
    )


def extract_original_publication_date(text: str) -> str | None:
    rows = re.findall(r"^\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|", text, flags=re.MULTILINE)
    for key, value in rows:
        if "發布日期" not in key and "原文日期" not in key and "更新日期" not in key:
            continue
        match = re.search(r"\b(20\d{2}-\d{2}-\d{2})\b", value)
        if match:
            return match.group(1)
    return None


def markdown_fence_for(markdown: str) -> str:
    """Return a fence that cannot be closed by a backtick run in the body."""

    longest = max((len(match.group(0)) for match in re.finditer(r"`+", markdown)), default=0)
    return "`" * max(3, longest + 1)


def source_title_for_infographic(source_slice_text: str) -> str | None:
    """Return a specific auditable title; otherwise make the locked AI title responsible."""

    for label in ("原文標題", "來源名稱"):
        match = re.search(
            rf"^\|\s*{re.escape(label)}\s*\|\s*(.+?)\s*\|\s*$",
            source_slice_text,
            flags=re.MULTILINE,
        )
        if match:
            title = match.group(1).strip()
            if title and not pending_infographic_value(title) and audited_title_is_specific(title):
                return title
    return None


def primary_work_title_for_infographic(source_slice_text: str) -> str | None:
    """Return the explicitly identified work that the source primarily interprets."""

    match = re.search(
        r"^\|\s*主要作品標題\s*\|\s*(.+?)\s*\|\s*$",
        source_slice_text,
        flags=re.MULTILINE,
    )
    if not match:
        return None
    title = match.group(1).strip()
    if not title or pending_infographic_value(title) or not audited_work_title_is_specific(title):
        return None
    return title


def audited_work_title_is_specific(title: str) -> bool:
    normalized = normalized_infographic_text(title).strip("《》〈〉\"'")
    return bool(normalized) and normalized not in {
        "使用者提供文字",
        "使用者提供圖片",
        "來源未標明",
        "visual source",
        "source",
    }


def audited_title_is_specific(title: str) -> bool:
    normalized = normalized_infographic_text(title)
    generic_titles = {
        "",
        "使用者提供文字",
        "使用者提供圖片",
        "來源未標明",
        "visual source",
        "source",
    }
    return (
        len(normalized) >= 6
        and normalized not in generic_titles
        and not normalized.startswith("ksvs")
        and not normalized.startswith("kstest")
    )


def source_label_for_footer(source_slice_text: str, source_title: str) -> str:
    """Use publisher plus an identifiable article title, never a bare brand."""

    publisher = ""
    url_match = re.search(r"^\|\s*URL\s*\|\s*(https?://[^|\s]+)", source_slice_text, flags=re.MULTILINE | re.IGNORECASE)
    if url_match:
        domain = urlsplit(url_match.group(1)).netloc.casefold().removeprefix("www.")
        known_domains = {
            "bnext.com.tw": "BusinessNext",
            "businessnext.com.tw": "BusinessNext",
            "openai.com": "OpenAI",
            "anthropic.com": "Anthropic",
            "obsidian.md": "Obsidian",
            "similarweb.com": "Similarweb",
            "unwire.hk": "unwire",
        }
        if domain in known_domains:
            publisher = known_domains[domain]
        elif domain:
            publisher = domain
    if publisher:
        page_title = re.sub(r"^#?\s*(?:KS-VS|KS-TEST)-\d+\s*[—-]\s*", "", source_title, flags=re.IGNORECASE).strip()
        page_title = re.sub(r"\s+", " ", page_title)
        if page_title.casefold().startswith(publisher.casefold()):
            page_title = page_title[len(publisher):].lstrip(" -—:：|｜")
        return f"{publisher}: {page_title[:52]}" if page_title else f"{publisher}: 未標題來源"
    return source_name_for_footer(source_title)


def source_name_for_footer(source_title: str) -> str:
    normalized = source_title.strip()
    normalized = re.sub(r"^#?\s*(?:KS-VS|KS-TEST)-\d+\s*[—-]\s*", "", normalized, flags=re.IGNORECASE).strip()
    lowered = normalized.casefold()
    if "openai" in lowered and "prompting 指南" in lowered and "中文" in normalized:
        return "OpenAI Prompting 指南（中文整理）"
    if "ai trends for 2026" in lowered and "change fitness" in lowered:
        return "HBS Working Knowledge: AI Trends for 2026"
    if "human-centred artificial intelligence msc" in lowered:
        return "Newcastle University: Human-Centred AI MSc"
    if "openai" in lowered and "frontier" in lowered and "hp" in lowered:
        return "OpenAI: HP Frontier partnership"
    if ("businessnext" in lowered or "數位時代" in normalized) and (
        "工作原型" in normalized or "ai product work archetypes" in lowered or "工程師與pm" in lowered
    ):
        return "BusinessNext: AI product work archetypes"
    known_sources = {
        "openai": "OpenAI",
        "anthropic": "Anthropic",
        "obsidian": "Obsidian",
        "similarweb": "Similarweb",
        "unwire": "unwire",
        "businessnext": "BusinessNext",
        "數位時代": "BusinessNext",
    }
    for marker, label in known_sources.items():
        if marker in lowered:
            topic = re.sub(marker, "", normalized, flags=re.IGNORECASE).strip(" -—:：|｜")
            topic = re.sub(r"\s+", " ", topic).strip()
            return f"{label}: {topic[:38]}" if topic else f"{label}: 未標題來源"
    # An ordinary hyphen can be part of the official title (for example,
    # "Human-Centred").  Only split on explicit title separators here.
    first = re.split(r"\s+[—-]\s+|[—:：|｜]", normalized, maxsplit=1)[0].strip()
    return first[:32] if first else "來源未標明"


def infographic_field(block: str, label: str) -> str:
    match = re.search(rf"^- {re.escape(label)}:(.*)$", block, flags=re.MULTILINE)
    return match.group(1).strip() if match else ""


def run_adapter(root: Path, source_slice: Path, vault: Path, max_kp: int, *extra: str) -> subprocess.CompletedProcess[str]:
    command = [
        sys.executable,
        str(ADAPTER),
        "--source-slice",
        str(source_slice.relative_to(root)),
        "--vault",
        str(vault.relative_to(root)),
        "--max-kp",
        str(max_kp),
        *extra,
    ]
    return subprocess.run(command, cwd=root, text=True, capture_output=True)


def run_outcome_generator(root: Path, vault: Path, source_id: str) -> subprocess.CompletedProcess[str]:
    command = [
        sys.executable,
        str(OUTCOME_GENERATOR),
        "--vault",
        str(vault.relative_to(root)),
        "--output-dir",
        str((vault / "outcome-pages").relative_to(root)),
        "--portal",
        str((vault / "成果頁總覽.md").relative_to(root)),
        "--incremental-source-id",
        source_id,
        "--json",
    ]
    return subprocess.run(command, cwd=root, text=True, capture_output=True)


def run_duplicate_review(root: Path, vault: Path) -> subprocess.CompletedProcess[str]:
    command = [
        sys.executable,
        str(ADAPTER),
        "--vault",
        str(vault.relative_to(root)),
        "--duplicate-review",
        "--json",
    ]
    return subprocess.run(command, cwd=root, text=True, capture_output=True)


def parse_json_stdout(result: subprocess.CompletedProcess[str], label: str) -> dict[str, object]:
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise PipelineStop("blocked", f"{label} did not return JSON: {result.stdout[:300]}") from exc


def resolve_inside(root: Path, target: Path) -> Path:
    resolved = (root / target).resolve() if not target.is_absolute() else target.resolve()
    if root != resolved and root not in resolved.parents:
        raise PipelineStop("blocked", f"refusing path outside project root: {target}")
    return resolved


def next_source_id(root: Path) -> str:
    max_seen = 0
    for path in root.glob("outputs/vertical_slice_*/KS-VS-*.md"):
        match = re.match(r"KS-VS-(\d+)", path.name)
        if match:
            max_seen = max(max_seen, int(match.group(1)))
    for path in root.glob(os.environ.get("CREATIVE_SECOND_BRAIN_VAULT", "knowledge-base-vault") + "sources/*/*/KS-VS-*.md"):
        match = re.match(r"KS-VS-(\d+)", path.name)
        if match:
            max_seen = max(max_seen, int(match.group(1)))
    return f"KS-VS-{max_seen + 1:04d}"


def slugify(value: str) -> str:
    candidate = value.strip()
    parsed = urlsplit(candidate)
    if parsed.netloc:
        candidate = (parsed.netloc + " " + parsed.path).strip("/")
    candidate = candidate.casefold()
    candidate = re.sub(r"[^a-z0-9]+", "_", candidate)
    candidate = re.sub(r"_+", "_", candidate).strip("_")
    return bounded_slug(candidate or "url_source", fallback="url_source")


def first_markdown_heading(markdown: str) -> str:
    for line in markdown.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return ""


def markdown_headings(markdown: str) -> list[str]:
    headings: list[str] = []
    for line in markdown.splitlines():
        if line.startswith("#"):
            headings.append(line.strip())
    return headings


def main_content_markdown(markdown: str) -> str:
    cleaned = markdown.strip()
    lines = cleaned.splitlines()
    for index, line in enumerate(lines):
        if line.startswith("# "):
            cleaned = "\n".join(lines[index:]).strip()
            break
    stop_markers = [
        "\nLinks to this page",
        "\nOn this page",
        "\nWas this helpful",
        "\nHelp us improve",
        "\nNot found\n",
        "\nThis page does not exist",
        "\nInteractive graph",
        "\n## Author",
        "\n## Keep reading",
        "\nKeep reading",
        "\n[View all]",
        "\n[PrevPrevious]",
        "\n[NextNext]",
        "\n## Share this article",
        "\n### Leave a Reply",
        "\nYour email address will not be published",
        "\n×!",
        "\npurchased [",
    ]
    stop_at = len(cleaned)
    for marker in stop_markers:
        found = cleaned.find(marker)
        if found > 0:
            stop_at = min(stop_at, found)
    cleaned = cleaned[:stop_at].strip()
    ui_noise_lines = {"Loading…", "Loading...", "Listen to article", "Share"}
    return "\n".join(line for line in cleaned.splitlines() if line.strip() not in ui_noise_lines).strip()


def canonical_body_fingerprint(markdown: str) -> str:
    return hashlib.sha256(markdown.strip().encode("utf-8")).hexdigest()


def representative_body_anchors(markdown: str) -> list[str]:
    anchors: list[str] = []
    lines = [line.strip() for line in markdown.splitlines() if line.strip()]
    for line in lines:
        if line.startswith("# "):
            anchors.append(line[:160])
            break
    numbered = [line for line in lines if re.match(r"^\d+\.\s+", line)]
    if numbered:
        anchors.append(numbered[0][:160])
        if numbered[-1] != numbered[0]:
            anchors.append(numbered[-1][:160])
    for line in lines:
        if line not in anchors and len(line) >= 40:
            anchors.append(line[:160])
            break
    deduped: list[str] = []
    for anchor in anchors:
        if anchor and anchor not in deduped:
            deduped.append(anchor)
    return deduped[:4]


def excerpt_text(markdown: str, limit: int = 2200) -> str:
    cleaned = markdown.strip()
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[:limit].rstrip() + "\n[...]"


def print_payload(payload: dict[str, object], as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    for key, value in payload.items():
        if isinstance(value, (dict, list)):
            print(f"{key}: {json.dumps(value, ensure_ascii=False)}")
        else:
            print(f"{key}: {value}")


if __name__ == "__main__":
    raise SystemExit(main())
