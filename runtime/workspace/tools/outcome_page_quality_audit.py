from __future__ import annotations
import os

import argparse
import hashlib
import html
import json
import re
import struct
import zlib
from html.parser import HTMLParser
from pathlib import Path

try:
    from tools.obsidian_vault_adapter import is_png_file, read_retained_original, retained_original_location
    from tools.reader_surface_guard import reader_safe_source_value, reader_surface_internal_message_patterns, reader_surface_internal_message_errors
    from tools.reader_shell import logo_for
    from tools.case_evidence_contract import CaseEvidenceError, _png_dimensions
    from tools.image_source_contract import ImageSourceContractError, identify_image
    from tools.outcome_page_generator import (
        case_evidence_reproduction_entrypoint_path,
        reader_title_from_saved_source,
        read_markdown_record,
        relative_url,
        research_reader_file,
        research_method_verification,
        research_reuse_work_path_issues,
        research_reuse_work_paths_for_source,
    )
except ModuleNotFoundError:
    from obsidian_vault_adapter import is_png_file, read_retained_original, retained_original_location  # type: ignore
    from reader_surface_guard import reader_safe_source_value, reader_surface_internal_message_patterns, reader_surface_internal_message_errors  # type: ignore
    from reader_shell import logo_for  # type: ignore
    from case_evidence_contract import CaseEvidenceError, _png_dimensions  # type: ignore
    from image_source_contract import ImageSourceContractError, identify_image  # type: ignore
    from outcome_page_generator import (  # type: ignore
        case_evidence_reproduction_entrypoint_path,
        reader_title_from_saved_source,
        read_markdown_record,
        relative_url,
        research_reader_file,
        research_method_verification,
        research_reuse_work_path_issues,
        research_reuse_work_paths_for_source,
    )


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_VAULT = REPO_ROOT / os.environ.get("CREATIVE_SECOND_BRAIN_VAULT", "knowledge-base-vault")
REQUIRED_PAGE_TEXTS = (
    "與你有何關係",
    "知識點",
)
SOURCE_OVERVIEW_HEADINGS = {
    "url": "這條 URL 講甚麼",
    "user_provided_text": "這份文字講甚麼",
    "user_provided_image": "這張圖片說甚麼",
}
# Source kinds without a public URL: their page proves provenance with a named source line instead.
DIRECT_SOURCE_KINDS = frozenset({"user_provided_text", "user_provided_image"})
PROVENANCE_LINE_RE = re.compile(r'<p class="[^"]*\bsource-provenance\b[^"]*"><span>[^<]+</span>(.*?)</p>', re.S)
INTERNAL_LANGUAGE_TERMS = (
    "本項目",
    "任務摘要",
    "AI 內部",
    "write/readback",
    "outcome page 前",
    "新機制樣本",
    "檢查頁面是否",
    "補完跨來源洞見判斷後再正式採納",
)
PLACEHOLDER_TERMS = (
    "[]",
    "<li>[]</li>",
    "<b>[]</b>",
    "待補",
)
FORBIDDEN_LAYOUT_HEADINGS = (
    "已扣連資料",
    "扣連到既有資料",
    "品味扣連",
    "個人化扣連",
    "重點速讀",
    "閱讀判斷",
)
FORBIDDEN_GENERIC_READER_TEXTS = (
    "先看資訊圖，再讀 URL 主旨。",
    "再看關係判斷、基準與更新時間。",
    "最後查看跨來源、品味與個人脈絡。",
    "先看資訊圖掌握原文結構，再用下方複利判斷與相關扣連決定是否深讀、採納或放入長期觀察。",
    "這條內容是否值得深讀、採納，或只需保留作背景觀察？",
    "它能否改變一個具體工作、學習、生活選擇或審美判斷？",
    "尚未形成跨來源洞見。",
    "尚未形成明確品味脈絡。",
    "尚未形成明確個人脈絡。",
    "舊資料只完成機制欄位補齊",
    "未作重新判斷",
)
FORBIDDEN_STYLE_TEXTS = (
    "本文探討了",
    "值得注意的是",
    "在當今時代",
    "隨著科技的發展",
    "總的來說",
    "綜上所述",
    "本工具認為",
)
MIN_DUPLICATE_TEXT_LENGTH = 30

# Where each audit_page result key can first be checked.  The completion receipt
# is blocked by any of them, but the plan locks first, so everything that is
# decided by the text of the plan itself must also run before the lock
# (ingest_rail.validate_plan calls reader_surface_text_findings on the
# reader-visible plan strings).  The rest needs the written Vault notes or the
# rendered page and can only run after the writer.
#   pre_lock          the same text checks run on the plan-derived strings
#   partial_pre_lock  run on the part the plan alone decides (the plan-derived
#                     strings; the knowledge-point claims for repeated content);
#                     the full page also has generated sections
#   post_writer_only  needs the written notes, assets or the rendered page
AUDIT_CHECK_STAGES = {
    "placeholder_issues": "pre_lock",
    "reader_surface_issues": "pre_lock",
    "generic_text_issues": "pre_lock",
    "style_issues": "pre_lock",
    "internal_language_risks": "pre_lock",
    "html_surface_issues": "partial_pre_lock",
    "missing_required_texts": "post_writer_only",
    "asset_issues": "post_writer_only",
    "source_url_issues": "post_writer_only",
    "navigation_issues": "post_writer_only",
    "layout_issues": "post_writer_only",
    "retired_taste_route_issues": "post_writer_only",
    "vault_body_layout_issues": "post_writer_only",
    "case_evidence_issues": "post_writer_only",
    "method_verification_issues": "post_writer_only",
    "research_reuse_issues": "post_writer_only",
    "infographic_status_issues": "post_writer_only",
    "duplicate_content_issues": "partial_pre_lock",
}


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit generated outcome pages for reader-facing quality.")
    parser.add_argument("--vault", type=Path, default=DEFAULT_VAULT)
    parser.add_argument("--source-id", help="Audit one current outcome and its entry; omit only for explicit whole-vault maintenance.")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    result = audit_source(args.vault, args.source_id) if args.source_id else audit_vault(args.vault)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"status: {result['status']}")
        print(f"pages: {result['page_count']}; needs_review: {result['needs_review_count']}")
    return 0


def audit_source(vault: Path, source_id: str) -> dict[str, object]:
    """Single-source ingest must not open historical outcome pages or sort old notes."""
    if not re.fullmatch(r"[A-Za-z0-9_-]+", source_id):
        raise ValueError("invalid source_id")
    vault = vault.resolve()
    output_dir = vault / "outcome-pages"
    page = output_dir / f"{source_id}_outcome.html"
    pages = [audit_page(page, vault)] if page.is_file() else []
    issues: list[str] = []
    index_path = output_dir / "index.html"
    catalogue = output_dir / "knowledge.html"
    if not index_path.is_file():
        issues.append("missing index.html")
    elif catalogue.is_file():
        index_text = index_path.read_text(encoding="utf-8")
        if 'href="knowledge.html"' not in index_text:
            issues.append("index does not link to knowledge.html")
        try:
            text = catalogue.read_text(encoding="utf-8")
            match = re.search(r'<script\b[^>]*id="catalog-data"[^>]*>(.*?)</script>', text, re.S)
            data = json.loads(match.group(1)) if match else {}
            if data.get("kind") != "knowledge" or not isinstance(data.get("items"), list):
                raise ValueError("invalid knowledge catalogue payload")
            matches = [item for item in data["items"] if isinstance(item, dict) and item.get("source_id") == source_id]
            if len(matches) != 1 or matches[0].get("url") != page.name:
                issues.append("current source must have exactly one canonical catalogue entry")
        except (OSError, ValueError, TypeError, AttributeError) as exc:
            issues.append("unreadable catalogue: " + str(exc))
    else:
        found = False
        for entry in discover_index_pages(index_path):
            if f'href="{page.name}"' in entry.read_text(encoding="utf-8"):
                found = True
                break
        if not found:
            issues.append("current source is missing from the outcome index")
    index = {"exists": index_path.is_file(), "status": "needs_review" if issues else "ready", "issues": issues, "scope": "current source entry"}
    portal = audit_portal(vault / "成果頁總覽.md")
    blocked = [] if pages else [{"reason": "missing_current_outcome", "path": str(page)}]
    count = sum(p["status"] != "ready" for p in pages)
    return {
        "status": "blocked" if blocked else ("needs_review" if count or issues or portal["status"] != "ready" else "ready"),
        "scope": "source_local", "source_id": source_id, "vault": str(vault),
        "index": index, "portal": portal, "page_count": len(pages),
        "ready_count": len(pages) - count, "needs_review_count": count,
        "pages": pages, "blocked_reasons": blocked,
    }


def audit_vault(vault: Path) -> dict[str, object]:
    vault = vault.resolve()
    output_dir = vault / "outcome-pages"
    index_path = output_dir / "index.html"
    portal_path = vault / "成果頁總覽.md"
    pages = sorted(output_dir.glob("*_outcome.html"))
    expected_order = expected_source_order(vault, pages)
    page_results = [audit_page(path, vault) for path in pages]
    needs_review_count = sum(1 for item in page_results if item["status"] != "ready")
    index_result = audit_index(index_path, len(pages), expected_order)
    portal_result = audit_portal(portal_path)
    blocked_reasons = []
    if not index_result["exists"]:
        blocked_reasons.append({"reason": "missing_index", "path": str(index_path)})
    if not portal_result["exists"]:
        blocked_reasons.append({"reason": "missing_portal", "path": str(portal_path)})
    if not pages:
        blocked_reasons.append({"reason": "missing_outcome_pages", "path": str(output_dir)})

    index_or_portal_needs_review = index_result["status"] != "ready" or portal_result["status"] != "ready"
    status = "blocked" if blocked_reasons else ("needs_review" if needs_review_count or index_or_portal_needs_review else "ready")
    return {
        "status": status,
        "vault": str(vault),
        "index": index_result,
        "portal": portal_result,
        "page_count": len(pages),
        "ready_count": len(pages) - needs_review_count,
        "needs_review_count": needs_review_count,
        "pages": page_results,
        "blocked_reasons": blocked_reasons,
        "rules": {
            "required_page_texts": list(REQUIRED_PAGE_TEXTS),
            "internal_language_terms": list(INTERNAL_LANGUAGE_TERMS),
            "placeholder_terms": list(PLACEHOLDER_TERMS),
            "forbidden_layout_headings": list(FORBIDDEN_LAYOUT_HEADINGS),
            "forbidden_generic_reader_texts": list(FORBIDDEN_GENERIC_READER_TEXTS),
            "forbidden_style_texts": list(FORBIDDEN_STYLE_TEXTS),
            "vault_body": "collapsed details with rendered markdown, not raw pre text",
            "taste_surface": "outcome HTML must not link to retired taste-app.html or launcher routes",
            "index_order": "newest_first_by_date_then_source_id",
        },
    }


def audit_index(index_path: Path, expected_count: int, expected_order: list[str]) -> dict[str, object]:
    if not index_path.exists():
        return {"exists": False, "status": "blocked", "issues": ["missing index.html"]}
    if (index_path.parent / "knowledge.html").is_file():
        return audit_catalogue_index(index_path, expected_count, expected_order)
    index_pages = discover_index_pages(index_path)
    page_texts = [path.read_text(encoding="utf-8") for path in index_pages]
    combined_html = "\n".join(page_texts)
    card_count = combined_html.count('<article class="card">')
    issues = []
    if any("成果頁總覽" not in page_text for page_text in page_texts):
        issues.append("missing index title")
    if any("HTML 只是閱讀門面" not in page_text for page_text in page_texts):
        issues.append("missing truth-boundary copy")
    if card_count != expected_count:
        issues.append(f"card count {card_count} does not match outcome pages {expected_count}")
    actual_order = unique_in_order(
        re.findall(r'href="([^"/]+)_outcome\.html"', combined_html)
    )
    if actual_order != expected_order:
        issues.append(f"index order is {actual_order}; expected {expected_order}")
    issues.extend(retired_taste_route_link_issues(combined_html, "index"))
    issues.extend(reader_surface_text_issues(visible_text(combined_html), "index"))
    return {
        "exists": True,
        "status": "ready" if not issues else "needs_review",
        "index_pages": [str(path) for path in index_pages],
        "card_count": card_count,
        "order": actual_order,
        "issues": issues,
    }


def audit_catalogue_index(index_path: Path, expected_count: int, expected_order: list[str]) -> dict[str, object]:
    """Validate the full catalog payload, not just one visible client-side page."""
    from urllib.parse import urlsplit, unquote
    paths = [index_path, index_path.parent / "knowledge.html", index_path.parent / "thinking.html"]
    issues, texts = [], []
    for path in paths:
        if not path.is_file():
            issues.append("missing catalogue page: " + path.name)
            continue
        text = path.read_text(encoding="utf-8")
        texts.append(text)
        for target in ("index.html", "knowledge.html", "thinking.html"):
            if f'href="{target}"' not in text:
                issues.append(path.name + ": missing shared navigation " + target)
        issues.extend(retired_taste_route_link_issues(text, "index"))
        issues.extend(reader_surface_text_issues(visible_text(text), "index"))
    items = []
    try:
        text = (index_path.parent / "knowledge.html").read_text(encoding="utf-8")
        match = re.search(r'<script\b[^>]*id="catalog-data"[^>]*>(.*?)</script>', text, re.S)
        data = json.loads(match.group(1)) if match else {}
        if data.get("kind") != "knowledge" or not isinstance(data.get("items"), list):
            raise ValueError("invalid knowledge catalogue payload")
        items = data["items"]
        for item in items:
            url = urlsplit(str(item.get("url", "")))
            target = (index_path.parent / unquote(url.path)).resolve()
            if url.scheme or url.netloc or target.parent != index_path.parent.resolve() or not target.is_file():
                issues.append("invalid or missing outcome URL: " + str(item.get("source_id", "")))
    except (OSError, ValueError, TypeError, AttributeError) as exc:
        issues.append("unreadable catalogue: " + str(exc))
    order = [str(item.get("source_id", "")) for item in items if isinstance(item, dict)]
    if len(items) != expected_count:
        issues.append(f"catalogue count {len(items)} does not match outcome pages {expected_count}")
    if order != expected_order:
        issues.append(f"index order is {order}; expected {expected_order}")
    return {"exists": True, "status": "needs_review" if issues else "ready",
            "index_pages": [str(path) for path in paths], "card_count": len(items),
            "order": order, "issues": issues, "scope": "catalogue payload, routes and reader copy"}


def discover_index_pages(index_path: Path) -> list[Path]:
    paginated = sorted(
        path
        for path in index_path.parent.glob("page-*.html")
        if re.fullmatch(r"page-\d{3}\.html", path.name)
    )
    return [index_path, *paginated]


def audit_portal(portal_path: Path) -> dict[str, object]:
    if not portal_path.exists():
        return {"exists": False, "status": "blocked", "issues": ["missing 成果頁總覽.md"]}
    text = portal_path.read_text(encoding="utf-8")
    issues = []
    if "成果頁總覽" not in text:
        issues.append("missing portal title")
    if "## 立即開始" not in text:
        issues.append("missing portal quick-start section")
    if "Codex inline visualizer" in text or "正式品味誌" in text:
        issues.append("retired Taste App text exposed in portal")
    if "(outcome-pages/index.html)" not in text:
        issues.append("missing HTML knowledge-results entry")
    if re.search(r"^\|\s*來源\s*\|", text, flags=re.MULTILINE):
        issues.append("portal must not duplicate source list")
    issues.extend(retired_taste_route_link_issues(text, "portal"))
    issues.extend(reader_surface_text_issues(text, "portal"))
    return {
        "exists": True,
        "status": "ready" if not issues else "needs_review",
        "issues": issues,
    }


def expected_source_order(vault: Path, pages: list[Path]) -> list[str]:
    source_ids = [path.name.replace("_outcome.html", "") for path in pages]
    return sorted(source_ids, key=lambda source_id: source_order_key(vault, source_id), reverse=True)


def source_order_key(vault: Path, source_id: str) -> tuple[int, int, str]:
    return (
        source_sort_date_number(vault, source_id),
        source_id_number(source_id),
        source_id,
    )


def source_sort_date_number(vault: Path, source_id: str) -> int:
    matches = sorted((vault / "sources").glob(f"**/{source_id}_*.md"))
    if not matches:
        return 0
    fields = read_frontmatter_fields(matches[0])
    for key in ("capture_date", "created", "date", "updated", "generated_at"):
        value = str(fields.get(key) or "").strip()
        if value:
            return sortable_date_number(value)
    return 0


def read_frontmatter_fields(path: Path) -> dict[str, str]:
    text = path.read_text(encoding="utf-8-sig")
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end == -1:
        return {}
    fields: dict[str, str] = {}
    for raw_line in text[3:end].splitlines():
        if ":" not in raw_line or raw_line.startswith(" "):
            continue
        key, value = raw_line.split(":", 1)
        fields[key.strip()] = value.strip().strip('"')
    return fields


def sortable_date_number(value: str) -> int:
    normalized = value.strip().replace("Z", "+00:00")
    try:
        return int(datetime_from_iso(normalized).strftime("%Y%m%d%H%M%S"))
    except ValueError:
        match = re.search(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})", normalized)
        if match:
            year, month, day = match.groups()
            return int(f"{int(year):04d}{int(month):02d}{int(day):02d}000000")
    return 0


def datetime_from_iso(value: str):
    from datetime import datetime

    return datetime.fromisoformat(value)


def source_id_number(source_id: str) -> int:
    match = re.search(r"(\d+)(?!.*\d)", source_id)
    return int(match.group(1)) if match else 0


def unique_in_order(items: list[str]) -> list[str]:
    unique_items: list[str] = []
    for item in items:
        if item not in unique_items:
            unique_items.append(item)
    return unique_items


def placeholder_terms_in(text: str) -> list[str]:
    # 待補足 describes a substantive gap; it is not the 待補 editing marker.
    return [term for term in PLACEHOLDER_TERMS if (
        bool(re.search(r"待補(?!足)", text)) if term == "待補" else term in text
    )]


def audit_page(page_path: Path, vault: Path) -> dict[str, object]:
    html_text = page_path.read_text(encoding="utf-8")
    reader_html = strip_vault_body_sections(html_text)
    text = visible_text(reader_html)
    source_input_kind = body_attribute(html_text, "data-source-input-kind")
    source_note = first_matching_source_note(vault, page_path.stem.replace("_outcome", ""))
    source_note_text = source_note.read_text(encoding="utf-8") if source_note else ""
    source_fields = frontmatter_fields(source_note_text) if source_note else {}
    asset_mode = str(source_fields.get("asset_mode") or "formal_infographic").strip().strip('"')
    missing_texts = [item for item in REQUIRED_PAGE_TEXTS if item not in text]
    overview_heading = SOURCE_OVERVIEW_HEADINGS.get(source_input_kind)
    if overview_heading and overview_heading not in text:
        missing_texts.append("missing source overview heading")
    asset_issues = missing_assets(
        html_text,
        page_path,
        vault,
        asset_mode=asset_mode,
        source_input_kind=source_input_kind,
        source_fields=source_fields,
        source_attachments=frontmatter_list(source_note_text, "source_attachments"),
    )
    direct_provenance_ok = has_direct_source_provenance(
        html_text,
        text,
        source_input_kind,
    )
    source_url_issues = [] if (
        re.search(r'<a href="https?://[^"]+"[^>]*>https?://', html_text)
        or direct_provenance_ok
    ) else ["missing source provenance"]
    navigation_issues = []
    if not re.search(r'<a href="index\.html"[^>]*>知識成果</a>', html_text):
        navigation_issues.append("missing back-to-index navigation")
    layout_issues = layout_semantic_issues(reader_html)
    retired_route_issues = retired_taste_route_link_issues(html_text, "page")
    vault_body_issues = vault_body_layout_issues(html_text)
    if source_note:
        vault_body_issues.extend(retained_source_issues(html_text, source_note.read_text(encoding="utf-8")))
    case_evidence_issues = case_evidence_source_local_issues(html_text, page_path, vault, source_note)
    method_verification_issues = research_method_verification_issues(html_text, page_path, vault)
    research_reuse_issues = research_reuse_source_local_issues(html_text, page_path, vault)
    infographic_status_issues = infographic_source_status_issues(page_path, vault)
    duplicate_content_issues = content_repetition_issues(reader_html)
    generic_text_issues = generic_reader_text_issues(text)
    style_issues = reader_style_issues(text)
    reader_surface_issues = reader_surface_text_issues(text, "page")
    html_surface_issues = reader_surface_internal_message_errors(
        [visible_text(html_text)],
        context="page_html",
    )
    internal_hits = [term for term in INTERNAL_LANGUAGE_TERMS if term in text]
    placeholder_issues = placeholder_terms_in(text + "\n" + reader_html)
    issues = (
        missing_texts
        + asset_issues
        + source_url_issues
        + navigation_issues
        + layout_issues
        + retired_route_issues
        + vault_body_issues
        + case_evidence_issues
        + method_verification_issues
        + research_reuse_issues
        + infographic_status_issues
        + duplicate_content_issues
        + generic_text_issues
        + style_issues
        + reader_surface_issues
        + html_surface_issues
        + placeholder_issues
    )
    status = "needs_review" if issues or internal_hits else "ready"
    return {
        "status": status,
        "path": str(page_path),
        "title": page_title(html_text),
        "missing_required_texts": missing_texts,
        "asset_issues": asset_issues,
        "source_url_issues": source_url_issues,
        "navigation_issues": navigation_issues,
        "layout_issues": layout_issues,
        "retired_taste_route_issues": retired_route_issues,
        "vault_body_layout_issues": vault_body_issues,
        "case_evidence_issues": case_evidence_issues,
        "method_verification_issues": method_verification_issues,
        "research_reuse_issues": research_reuse_issues,
        "infographic_status_issues": infographic_status_issues,
        "duplicate_content_issues": duplicate_content_issues,
        "generic_text_issues": generic_text_issues,
        "style_issues": style_issues,
        "reader_surface_issues": reader_surface_issues,
        "html_surface_issues": html_surface_issues,
        "placeholder_issues": placeholder_issues,
        "internal_language_risks": internal_hits,
        "issue_count": len(issues),
        "risk_count": len(internal_hits),
    }


def retained_source_issues(html_text: str, source_note: str) -> list[str]:
    try:
        mode = (frontmatter_fields(source_note).get("source_retention_mode") or "").strip().strip('"') or None
        if retained_original_location(mode) != "source_note_body":
            return []
        canonical = read_retained_original(mode, note_text=source_note)
    except ValueError:
        return []
    if not canonical:
        return []
    rendered = [html.unescape(value).strip() for value in re.findall(
        r'<pre class="note-code"><code>(.*?)</code></pre>', html_text, re.S)]
    return [] if canonical in rendered else ["retained full source body is incomplete in outcome page"]


def infographic_source_status_issues(page_path: Path, vault: Path) -> list[str]:
    source_id = page_path.stem.replace("_outcome", "")
    if not source_id:
        return []
    source_note = first_matching_source_note(vault, source_id)
    if source_note is None:
        return []
    text = source_note.read_text(encoding="utf-8")
    fields = frontmatter_fields(text)
    if str(fields.get("asset_mode") or "").strip().strip('"') in {"case_evidence", "knowledge_only"}:
        # Case evidence has its own screenshot/reproduction manifest; it must
        # not be marked down for formal gpt-image-2 receipt fields.
        return []
    issues = []
    for key in ("png_semantic_validation_status", "png_reverse_review_status"):
        value = str(fields.get(key) or "").strip().strip('"').casefold()
        if value and value not in {"passed", "pass"}:
            issues.append(f"source infographic {key} is {value}")
    return issues


def case_evidence_source_local_issues(
    html_text: str,
    page_path: Path,
    vault: Path,
    source_note: Path | None,
) -> list[str]:
    if source_note is None:
        return []
    source_fields, source_body = read_markdown_record(source_note)
    if str(source_fields.get("asset_mode") or "") != "case_evidence":
        return []
    issues: list[str] = []
    expected_title = reader_title_from_saved_source(source_fields, source_body)
    if expected_title and page_title(html_text) != expected_title:
        issues.append("case evidence outcome title does not match saved source title")
    try:
        entrypoint = case_evidence_reproduction_entrypoint_path(
            vault,
            page_path.stem.replace("_outcome", ""),
            source_fields,
        )
    except ValueError as exc:
        issues.append(str(exc))
    else:
        assert entrypoint is not None
        entrypoint_href = relative_url(entrypoint, page_path.parent)
        if f'href="{entrypoint_href}"' not in html_text:
            issues.append("case evidence reproduction entrypoint is not linked from outcome page")
    return issues


def research_method_verification_issues(html_text: str, page_path: Path, vault: Path) -> list[str]:
    """Keep an honest demoted method from receiving a reader-quality green light."""
    source_id = page_path.stem.replace("_outcome", "")
    verification = research_method_verification(vault, source_id)
    if verification.get("state") != "needs_revalidation":
        return []
    issues = [f"{source_id} research method is marked needs_revalidation"]
    if "方法驗證待重驗" not in html_text:
        issues.append(f"{source_id} outcome page does not disclose method revalidation")
    if "查看歷史本地試驗（未通過方法驗證）" not in html_text:
        issues.append(f"{source_id} outcome page labels a failed method trial as a normal preview")
    return issues


def research_reuse_source_local_issues(html_text: str, page_path: Path, vault: Path) -> list[str]:
    source_id = page_path.stem.replace("_outcome", "")
    issues = research_reuse_work_path_issues(vault, source_id)
    for path in research_reuse_work_paths_for_source(vault, source_id):
        target = research_reader_file(vault, path)
        if target is None:
            continue
        href = relative_url(target, page_path.parent)
        if f'href="{href}"' not in html_text:
            issues.append(f"{source_id} research reuse work path is not linked from outcome page: {path}")
    return issues


def first_matching_source_note(vault: Path, source_id: str) -> Path | None:
    source_root = vault / "sources"
    if not source_root.exists():
        return None
    matches = sorted(source_root.glob(f"**/{source_id}_*.md"))
    return matches[0] if matches else None


def frontmatter_fields(text: str) -> dict[str, str]:
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end == -1:
        return {}
    fields: dict[str, str] = {}
    for line in text[3:end].splitlines():
        if ":" not in line or line.startswith(" "):
            continue
        key, value = line.split(":", 1)
        fields[key.strip()] = value.strip()
    return fields


def frontmatter_list(text: str, key: str) -> list[str]:
    """Read one flat YAML string list (``key:`` then ``  - "item"`` lines)."""
    if not text.startswith("---"):
        return []
    end = text.find("\n---", 3)
    lines = text[3:end if end != -1 else 3].splitlines()
    items, inside = [], False
    for line in lines:
        if not line.startswith(" "):
            inside = line.split(":", 1)[0].strip() == key
            continue
        if inside and line.lstrip().startswith("- "):
            items.append(line.lstrip()[2:].strip().strip('"').strip("'"))
    return items


def body_attribute(html_text: str, attribute: str) -> str:
    body_match = re.search(r"<body\b([^>]*)>", html_text, flags=re.IGNORECASE)
    if not body_match:
        return ""
    attribute_match = re.search(
        rf'\b{re.escape(attribute)}="([^"]*)"',
        body_match.group(1),
        flags=re.IGNORECASE,
    )
    return html.unescape(attribute_match.group(1)).strip() if attribute_match else ""


def strip_vault_body_sections(html_text: str) -> str:
    return re.sub(
        r'<section class="vault-body">[\s\S]*?</section>',
        " ",
        html_text,
        flags=re.IGNORECASE,
    )


def has_direct_source_provenance(
    html_text: str,
    visible_page_text: str,
    source_input_kind: str,
) -> bool:
    """The reader sees the content's source named on its provenance line; the identity stays a machine attribute."""
    if source_input_kind not in DIRECT_SOURCE_KINDS:
        return False
    source_identity = body_attribute(html_text, "data-source-identity")
    line = PROVENANCE_LINE_RE.search(html_text)
    line_text = html.unescape(re.sub(r"<[^>]+>", "", line.group(1))).strip() if line else ""
    named = bool(reader_safe_source_value(line_text, fallback=""))
    return bool(re.fullmatch(rf"{re.escape(source_input_kind)}:[0-9a-f]{{64}}", source_identity) and named)


def _field_text(fields: dict[str, str] | None, key: str) -> str:
    return str((fields or {}).get(key) or "").strip().strip('"').strip("'").strip()


def registered_original_image(
    vault: Path,
    source_input_kind: str,
    source_fields: dict[str, str] | None,
) -> Path | None:
    """Return the source note's registered original image, or None.

    Only a user-provided-image source owns an original, and it is named by the
    source note's ``source_original_image`` field (the same field the outcome
    generator and the vault writer use), never guessed from a file name.
    """
    kind = _field_text(source_fields, "source_input_kind") or source_input_kind
    if kind != "user_provided_image":
        return None
    relative = _field_text(source_fields, "source_original_image")
    if not relative:
        return None
    original = (vault / relative).resolve()
    return original if vault in original.parents else None


def _png_stream_issue(raw: bytes) -> str | None:
    """Check a complete PNG of any colour type, bit depth or interlace."""
    if len(raw) < 45 or not raw.startswith(b"\x89PNG\r\n\x1a\n"):
        return "not a PNG file"
    offset, first, idat, end_seen = 8, True, [], False
    while offset < len(raw) and not end_seen:
        if offset + 12 > len(raw):
            return "truncated PNG chunk"
        length = struct.unpack(">I", raw[offset:offset + 4])[0]
        kind = raw[offset + 4:offset + 8]
        end = offset + 12 + length
        if end > len(raw):
            return "truncated PNG chunk"
        payload = raw[offset + 8:end - 4]
        if zlib.crc32(kind + payload) & 0xFFFFFFFF != struct.unpack(">I", raw[end - 4:end])[0]:
            return "PNG chunk checksum mismatch"
        if first:
            if kind != b"IHDR" or length != 13:
                return "PNG does not start with a valid header"
            width, height, depth, colour = struct.unpack(">IIBB", payload[:10])
            allowed = {0: {1, 2, 4, 8, 16}, 2: {8, 16}, 3: {1, 2, 4, 8}, 4: {8, 16}, 6: {8, 16}}
            if width <= 0 or height <= 0 or depth not in allowed.get(colour, set()):
                return "PNG header is invalid"
            first = False
        elif kind == b"IDAT":
            idat.append(payload)
        elif kind == b"IEND":
            end_seen = True
        offset = end
    if not end_seen or offset != len(raw):
        return "PNG is truncated or has trailing bytes"
    if not idat:
        return "PNG has no image data"
    decoder = zlib.decompressobj()
    try:
        for chunk in idat:
            decoder.decompress(chunk)
        decoder.flush()
    except zlib.error:
        return "PNG image data cannot be decoded"
    return None if decoder.eof else "PNG image data is incomplete"


def _jpeg_stream_issue(raw: bytes) -> str | None:
    body = raw.rstrip(b"\x00")
    if len(body) < 4 or not body.startswith(b"\xff\xd8") or not body.endswith(b"\xff\xd9"):
        return "JPEG is truncated or has no end marker"
    cursor, frame_seen = 2, False
    while cursor + 4 <= len(body):
        if body[cursor] != 0xFF:
            return "JPEG segment structure is invalid"
        marker = body[cursor + 1]
        if marker == 0xFF:
            cursor += 1
            continue
        cursor += 2
        if marker == 0x01 or 0xD0 <= marker <= 0xD7:
            continue
        length = struct.unpack(">H", body[cursor:cursor + 2])[0]
        if length < 2 or cursor + length > len(body):
            return "JPEG segment is truncated"
        if marker in {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}:
            frame_seen = True
        if marker == 0xDA:
            return None if frame_seen and len(body) > cursor + length + 2 else "JPEG has no image data"
        cursor += length
    return "JPEG has no image data"


def _webp_stream_issue(raw: bytes) -> str | None:
    if len(raw) < 20 or raw[:4] != b"RIFF" or raw[8:12] != b"WEBP":
        return "not a WebP file"
    declared = struct.unpack("<I", raw[4:8])[0] + 8
    if declared not in {len(raw), len(raw) + 1}:
        return "WebP size does not match the file"
    cursor, kinds = 12, set()
    while cursor < len(raw):
        if cursor + 8 > len(raw):
            return "WebP chunk is truncated"
        size = struct.unpack("<I", raw[cursor + 4:cursor + 8])[0]
        if cursor + 8 + size > len(raw):
            return "WebP chunk is truncated"
        kinds.add(raw[cursor:cursor + 4])
        cursor += 8 + size + (size & 1)
    return None if kinds & {b"VP8 ", b"VP8L", b"ANMF"} else "WebP has no image data"


def user_image_structure_issue(raw: bytes) -> str | None:
    """Why user-supplied image bytes are not one complete PNG, JPEG or WebP, or None."""
    try:
        mime_type = identify_image(raw)[0]
    except ImageSourceContractError:
        return "is not a readable PNG, JPEG or WebP file"
    structure = {"image/png": _png_stream_issue, "image/jpeg": _jpeg_stream_issue, "image/webp": _webp_stream_issue}
    problem = structure[mime_type](raw)
    return f"is incomplete or corrupt ({problem})" if problem else None


def attachment_image_issue(path: Path) -> str | None:
    """Validate a declared source attachment: user evidence like an original.

    Its bytes are kept as supplied (PNG, JPEG or WebP), and its file name starts
    with the first 12 hex digits of its SHA-256, which binds name to content.
    """
    try:
        raw = path.read_bytes()
    except OSError:
        return "source attachment is unreadable"
    problem = user_image_structure_issue(raw)
    if problem:
        return f"source attachment {problem}"
    if not hashlib.sha256(raw).hexdigest().startswith(path.stem):
        return "source attachment does not match the hash in its file name"
    return None


def original_image_issue(path: Path, source_fields: dict[str, str] | None) -> str | None:
    """Validate a user-provided original: complete PNG/JPEG/WebP bound to its registered hash.

    An original is preserved byte for byte, so (unlike a generated infographic)
    it may use any PNG colour type, including alpha.  Only its structure and
    its registered SHA-256 / MIME are checked.
    """
    try:
        raw = path.read_bytes()
    except OSError:
        return "original image is unreadable"
    try:
        mime_type = identify_image(raw)[0]
    except ImageSourceContractError:
        return "original image is not a readable PNG, JPEG or WebP file"
    structure = {"image/png": _png_stream_issue, "image/jpeg": _jpeg_stream_issue, "image/webp": _webp_stream_issue}
    problem = structure[mime_type](raw)
    if problem:
        return f"original image is incomplete or corrupt ({problem})"
    expected = _field_text(source_fields, "source_image_sha256")
    if not re.fullmatch(r"[0-9a-f]{64}", expected):
        return "original image has no registered SHA-256"
    if hashlib.sha256(raw).hexdigest() != expected:
        return "original image does not match its registered SHA-256"
    declared_mime = _field_text(source_fields, "source_image_mime")
    if declared_mime and declared_mime != mime_type:
        return "original image type does not match its registered MIME type"
    return None


def missing_assets(
    html_text: str,
    page_path: Path,
    vault: Path,
    *,
    asset_mode: str = "formal_infographic",
    source_input_kind: str = "",
    source_fields: dict[str, str] | None = None,
    source_attachments: list[str] | None = None,
) -> list[str]:
    issues = []
    image_sources = re.findall(r'<img\s+[^>]*src="([^"]+)"', html_text)
    attachments = {(vault / item).resolve() for item in source_attachments or []}
    if not image_sources:
        return [] if asset_mode == "knowledge_only" else ["missing infographic image"]
    # The shared reader shell supplies one project-owned brand PNG.  It keeps
    # existing reader pages visually stable while the outcome audit continues
    # to reject every other path outside the vault.
    reader_logo = logo_for(vault)
    approved_brand = None
    if (
        reader_logo is not None
        and reader_logo.is_file()
        and REPO_ROOT in reader_logo.resolve().parents
    ):
        try:
            # Brand artwork can legitimately use alpha; validate its complete
            # PNG stream instead of applying the opaque-infographic rule.
            _png_dimensions(reader_logo)
            approved_brand = reader_logo.resolve()
        except (CaseEvidenceError, OSError):
            pass
    # The source note's registered original image is user evidence, not a
    # generated infographic; it alone is exempt from the opaque-PNG rule.
    original = registered_original_image(vault, source_input_kind, source_fields)
    for source in image_sources:
        candidate = (page_path.parent / html.unescape(source)).resolve()
        if vault not in candidate.parents and candidate != vault:
            if candidate != approved_brand:
                issues.append(f"image outside vault: {source}")
        elif not candidate.exists():
            issues.append(f"missing image file: {source}")
        elif original is not None and candidate == original:
            problem = original_image_issue(candidate, source_fields)
            if problem:
                issues.append(f"{problem}: {source}")
        elif candidate in attachments:
            problem = attachment_image_issue(candidate)
            if problem:
                issues.append(f"{problem}: {source}")
        elif not is_png_file(candidate):
            issues.append(f"invalid PNG image file: {source}")
    return issues


def visible_text(html_text: str) -> str:
    without_tags = re.sub(r"<script[\s\S]*?</script>|<style[\s\S]*?</style>", " ", html_text)
    without_tags = re.sub(r"<[^>]+>", " ", without_tags)
    return html.unescape(re.sub(r"\s+", " ", without_tags)).strip()


def reader_surface_text_issues(text: str, surface: str) -> list[str]:
    issues = []
    issues.extend(reader_surface_internal_message_errors([text], context=surface))
    for term in INTERNAL_LANGUAGE_TERMS:
        if term in text:
            issues.append(f"{surface} internal-language risk: {term}")
    for term in FORBIDDEN_GENERIC_READER_TEXTS:
        if term in text:
            issues.append(f"{surface} forbidden generic reader text: {term}")
    for term in FORBIDDEN_STYLE_TEXTS:
        if term in text:
            issues.append(f"{surface} forbidden reader style text: {term}")
    for term in placeholder_terms_in(text):
        issues.append(f"{surface} placeholder text: {term}")
    return issues


def reader_surface_text_findings(text: str) -> list[tuple[str, str]]:
    """The text checks of ``reader_surface_text_issues`` as (kind, detail) pairs.

    ``text`` is normalised like ``visible_text`` output (collapsed whitespace).
    A plan-derived string fails before the lock on exactly the findings the
    post-writer audit would raise for the same text; the tests keep this
    function in step with ``reader_surface_text_issues``.
    """
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    findings: list[tuple[str, str]] = []
    if text:
        for code, pattern in reader_surface_internal_message_patterns():
            if pattern.search(text):
                findings.append(("internal_message", code))
                break
    findings += [("internal_language", term) for term in INTERNAL_LANGUAGE_TERMS if term in text]
    findings += [("forbidden_generic", term) for term in FORBIDDEN_GENERIC_READER_TEXTS if term in text]
    findings += [("forbidden_style", term) for term in FORBIDDEN_STYLE_TEXTS if term in text]
    findings += [("placeholder", term) for term in placeholder_terms_in(text)]
    return findings


def layout_semantic_issues(html_text: str) -> list[str]:
    headings = [
        html.unescape(re.sub(r"<[^>]+>", "", match)).strip()
        for match in re.findall(r"<h[23][^>]*>([\s\S]*?)</h[23]>", html_text)
    ]
    issues = [
        f"forbidden duplicate link heading: {heading}"
        for heading in headings
        if any(heading == forbidden or heading.endswith(forbidden) for forbidden in FORBIDDEN_LAYOUT_HEADINGS)
    ]
    related_count = sum(1 for heading in headings if "相關扣連" in heading)
    if related_count > 1:
        issues.append(f"related link section count is {related_count}")
    if related_count == 1 and not any(heading in headings for heading in ("複利扣連", "跨來源洞見", "品味脈絡", "個人脈絡")):
        issues.append("related link section has no visible group")
    return issues


def retired_taste_route_link_issues(text: str, surface: str) -> list[str]:
    issues = []
    if re.search(r'href="[^"]*taste-app\.html', text, flags=re.IGNORECASE) or re.search(
        r"\]\([^)]*taste-app\.html", text, flags=re.IGNORECASE
    ):
        issues.append(f"{surface} links to retired taste-app.html")
    if re.search(r"\]\(file:///[^)]*開啟品味誌\.cmd\)", text, flags=re.IGNORECASE) or re.search(
        r'href="file:///[^"]*開啟品味誌\.cmd', text, flags=re.IGNORECASE
    ):
        issues.append(f"{surface} links to retired Taste App launcher")
    return issues


def vault_body_layout_issues(html_text: str) -> list[str]:
    match = re.search(
        r'<section class="vault-body">[\s\S]*?</section>',
        html_text,
        flags=re.IGNORECASE,
    )
    if not match:
        return []
    section_html = match.group(0)
    issues = []
    if re.search(r"<details\b[^>]*\bopen\b", section_html, flags=re.IGNORECASE):
        issues.append("vault body details must be collapsed by default")
    if "<pre>" in section_html.lower():
        issues.append("vault body must render markdown instead of raw pre text")
    if 'class="note-rendered"' not in section_html:
        issues.append("vault body missing rendered markdown container")
    return issues


def generic_reader_text_issues(text: str) -> list[str]:
    return [
        f"forbidden generic reader text: {term}"
        for term in FORBIDDEN_GENERIC_READER_TEXTS
        if term in text
    ]


def reader_style_issues(text: str) -> list[str]:
    return [
        f"forbidden reader style text: {term}"
        for term in FORBIDDEN_STYLE_TEXTS
        if term in text
    ]


def content_repetition_issues(html_text: str) -> list[str]:
    # A provenance line may legitimately be the work title shown in the H1.
    # Exempt that line alone; all other reader-visible duplication still counts.
    blocks = visible_blocks(PROVENANCE_LINE_RE.sub("", html_text))
    normalized_blocks = [
        block
        for block in (normalize_visible_block(text) for _, text in blocks)
        if len(block) >= MIN_DUPLICATE_TEXT_LENGTH
    ]
    issues: list[str] = []
    counts: dict[str, int] = {}
    for text in normalized_blocks:
        counts[text] = counts.get(text, 0) + 1
    for text, count in counts.items():
        if count > 1:
            issues.append(f"repeated visible content x{count}: {shorten(text)}")

    unique_blocks = list(dict.fromkeys(normalized_blocks))
    for index, left in enumerate(unique_blocks):
        for right in unique_blocks[index + 1 :]:
            short, long = (left, right) if len(left) <= len(right) else (right, left)
            if len(short) >= 45 and short in long:
                issues.append(f"contained visible content: {shorten(short)}")
    return issues


class VisibleBlockParser(HTMLParser):
    BLOCK_TAGS = {"h1", "h2", "h3", "p", "li", "strong", "b"}

    def __init__(self) -> None:
        super().__init__()
        self.skip_depth = 0
        self.current_tag: str | None = None
        self.current_text = ""
        self.blocks: list[tuple[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style"}:
            self.skip_depth += 1
            return
        if self.skip_depth:
            return
        if tag in self.BLOCK_TAGS:
            self.current_tag = tag
            self.current_text = ""

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style"} and self.skip_depth:
            self.skip_depth -= 1
            return
        if self.skip_depth:
            return
        if tag == self.current_tag:
            text = normalize_visible_block(self.current_text)
            if text:
                self.blocks.append((tag, text))
            self.current_tag = None
            self.current_text = ""

    def handle_data(self, data: str) -> None:
        if self.skip_depth or self.current_tag is None:
            return
        self.current_text += data


def visible_blocks(html_text: str) -> list[tuple[str, str]]:
    parser = VisibleBlockParser()
    parser.feed(html_text)
    return parser.blocks


def normalize_visible_block(text: str) -> str:
    text = re.sub(r"^#+\s*", "", html.unescape(text).strip())
    return re.sub(r"\s+", " ", text).strip()


def shorten(text: str, limit: int = 96) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def page_title(html_text: str) -> str:
    match = re.search(r"<h1[^>]*>([\s\S]*?)</h1>", html_text)
    return html.unescape(re.sub(r"<[^>]+>", "", match.group(1))).strip() if match else ""


if __name__ == "__main__":
    raise SystemExit(main())
