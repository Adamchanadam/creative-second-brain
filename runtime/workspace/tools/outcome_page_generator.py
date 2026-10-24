from __future__ import annotations
import os

import argparse
import html
import json
import re
import time
from datetime import datetime
from pathlib import Path

try:
    from tools.validation_manifest import build_embedded_manifest
except ModuleNotFoundError:
    from validation_manifest import build_embedded_manifest  # type: ignore

try:
    from tools.obsidian_vault_adapter import compounding_metadata_issues, frontmatter_fields, retained_note_body_section, TRANSCRIPT_ARCHIVE_MODE
    from tools.source_identity import normalize_url as normalize_public_url
    from tools.reader_surface_guard import (
        has_reader_surface_internal_message,
        reader_source_attribution_details,
        reader_safe_source_value,
        reader_source_attribution_label,
    )
except ModuleNotFoundError:
    from obsidian_vault_adapter import compounding_metadata_issues, frontmatter_fields, retained_note_body_section, TRANSCRIPT_ARCHIVE_MODE  # type: ignore
    from source_identity import normalize_url as normalize_public_url  # type: ignore
    from reader_surface_guard import (  # type: ignore
        has_reader_surface_internal_message,
        reader_source_attribution_details,
        reader_safe_source_value,
        reader_source_attribution_label,
    )


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_VAULT = REPO_ROOT / os.environ.get("CREATIVE_SECOND_BRAIN_VAULT", "knowledge-base-vault")
DEFAULT_OUTPUT_DIR = REPO_ROOT / "outputs" / "outcome_pages"
DEFAULT_VAULT_OUTPUT_DIR = DEFAULT_VAULT / "outcome-pages"
DEFAULT_PORTAL = DEFAULT_VAULT / "成果頁總覽.md"
DEFAULT_INDEX_PAGE_SIZE = 20


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate a read-only local outcome page from Obsidian vault data.")
    parser.add_argument("--source-id", action="append")
    parser.add_argument("--root", type=Path, default=REPO_ROOT, help="Verified installed workspace root for completed-source refreshes.")
    parser.add_argument(
        "--incremental-source-id",
        help="Regenerate one source outcome page, then incrementally rebuild the index without regenerating historical outcome pages.",
    )
    parser.add_argument("--vault", type=Path, default=DEFAULT_VAULT)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--index", action="store_true", help="Generate outcome pages, an HTML index, and the Obsidian portal.")
    parser.add_argument("--refresh-index", action="store_true", help="Refresh the catalogue and thinking readers without rewriting existing source outcome pages.")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_VAULT_OUTPUT_DIR)
    parser.add_argument("--portal", type=Path, default=DEFAULT_PORTAL)
    parser.add_argument("--page-size", type=int, default=DEFAULT_INDEX_PAGE_SIZE)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    if args.refresh_index and (args.index or args.incremental_source_id or args.source_id or args.output):
        parser.error("--refresh-index cannot be combined with source generation options")
    if args.refresh_index:
        result = refresh_outcome_index(vault=args.vault, output_dir=args.output_dir,
                                       portal=args.portal, page_size=args.page_size)
    elif args.incremental_source_id:
        result = generate_incremental_outcome(
            source_id=args.incremental_source_id,
            vault=args.vault,
            output_dir=args.output_dir,
            portal=args.portal,
            page_size=args.page_size,
            root=args.root,
        )
    elif args.index:
        source_ids = args.source_id or discover_source_ids(args.vault)
        result = generate_outcome_index(
            source_ids=source_ids,
            vault=args.vault,
            output_dir=args.output_dir,
            portal=args.portal,
            page_size=args.page_size,
            root=args.root,
        )
    else:
        if not args.source_id:
            parser.error("--source-id is required unless --index is used")
        source_ids = list(dict.fromkeys(args.source_id))
        if len(source_ids) > 1:
            if args.output:
                parser.error("--output accepts one source only; use --output-dir for multiple sources")
            results = [generate_outcome_page(source_id=source_id, vault=args.vault,
                       output=args.output_dir / f"{source_id}_outcome.html", root=args.root) for source_id in source_ids]
            result = {"status": "ready", "output_dir": str(args.output_dir), "results": results}
        else:
            output_dir_requested = any(
                value == "--output-dir" or value.startswith("--output-dir=") for value in os.sys.argv[1:]
            )
            output = args.output or (args.output_dir / f"{source_ids[0]}_outcome.html" if output_dir_requested else None)
            result = generate_outcome_page(source_id=source_ids[0], vault=args.vault, output=output, root=args.root)
    if not (args.refresh_index or args.incremental_source_id or args.index):
        try:
            from .knowledge_nebula import refresh_installed
        except ImportError:
            from knowledge_nebula import refresh_installed
        paused = paused_nebula(refresh_installed(args.vault, Path(result['output']).parent if result.get('output') else args.output_dir))
        if paused:
            result["nebula"] = paused
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"status: {result['status']}")
        output = result.get("output") or result.get("current_page") or result.get("html_index") or result.get("output_dir")
        print(f"output: {output}")
        paused = paused_nebula(result.get("nebula"))
        if paused:
            print(f"nebula: paused - {paused['reason']}")
    return 0


def discover_source_ids(vault: Path) -> list[str]:
    vault = vault.resolve()
    source_ids: list[str] = []
    for path in sorted((vault / "applications").glob("**/APP-KS-VS-*.md")):
        fields, _ = read_markdown_record(path)
        source_id = str(fields.get("source_id") or "").strip()
        if source_id:
            source_ids.append(source_id)
    return sorted(set(source_ids))


def generate_outcome_index(
    *,
    source_ids: list[str],
    vault: Path,
    output_dir: Path = DEFAULT_VAULT_OUTPUT_DIR,
    portal: Path = DEFAULT_PORTAL,
    page_size: int = DEFAULT_INDEX_PAGE_SIZE,
    root: Path = REPO_ROOT,
) -> dict[str, object]:
    if page_size < 1:
        raise ValueError("page_size must be greater than 0")
    vault = vault.resolve()
    output_dir = output_dir.resolve()
    portal = portal.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    portal.parent.mkdir(parents=True, exist_ok=True)

    entries = []
    page_results = []
    preserved_existing_pages = []
    blocked_reasons = []
    for source_id in source_ids:
        page_path = output_dir / f"{source_id}_outcome.html"
        try:
            page_results.append(generate_outcome_page(source_id, vault, page_path, root=root))
        except SystemExit as exc:
            # Preserve an existing historical result when it cannot be
            # regenerated, so it cannot block a different source's ingest.
            if page_path.exists():
                entries.append(outcome_index_entry(source_id, vault, page_path))
                preserved_existing_pages.append({"source_id": source_id, "reason": str(exc)})
                continue
            blocked_reasons.append({"source_id": source_id, "reason": str(exc)})
            continue
        entries.append(outcome_index_entry(source_id, vault, page_path))
    cleaned_retired_routes = cleanup_retired_taste_routes(output_dir)
    entries = sort_entries_newest_first(entries)

    nebula_report: dict[str, object] = {}
    index_pages = write_paginated_index(entries, output_dir, page_size, vault=vault, nebula_report=nebula_report)
    index_path = index_pages[0] if index_pages else output_dir / "index.html"
    portal.write_text(render_portal_markdown(entries, portal, vault), encoding="utf-8")
    paused = paused_nebula(nebula_report.get("nebula"))

    return {
        "status": "ready" if not blocked_reasons else "needs_review",
        "source_count": len(entries),
        "page_size": page_size,
        "page_count": max(1, (len(entries) + page_size - 1) // page_size),
        "output_dir": str(output_dir),
        "html_index": str(index_path),
        "index_pages": [str(path) for path in index_pages],
        "obsidian_portal": str(portal),
        "pages": [result["output"] for result in page_results],
        "preserved_existing_pages": preserved_existing_pages,
        "cleaned_retired_taste_routes": [str(path) for path in cleaned_retired_routes],
        "blocked_reasons": blocked_reasons,
        "validation_manifest": outcome_manifest(),
        **({"nebula": paused} if paused else {}),
    }


def generate_incremental_outcome(
    *,
    source_id: str,
    vault: Path,
    output_dir: Path = DEFAULT_VAULT_OUTPUT_DIR,
    portal: Path = DEFAULT_PORTAL,
    page_size: int = DEFAULT_INDEX_PAGE_SIZE,
    root: Path = REPO_ROOT,
) -> dict[str, object]:
    """Refresh one outcome page and rebuild only the lightweight reader index.

    Historical pages are treated as already-published projections.  Reading
    their source/application metadata is sufficient to retain their cards in
    the index; they must not be regenerated as a side effect of a new source
    ingest.  A missing or unreadable historical page is a warning, never a
    blocker for the current source's completed outcome.
    """
    if page_size < 1:
        raise ValueError("page_size must be greater than 0")
    vault = vault.resolve()
    output_dir = output_dir.resolve()
    portal = portal.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    portal.parent.mkdir(parents=True, exist_ok=True)

    current_page = output_dir / f"{source_id}_outcome.html"
    current_result = generate_outcome_page(source_id, vault, current_page, root=root)
    cleaned_retired_routes = cleanup_retired_taste_routes(output_dir)
    entries: list[dict[str, object]] = []
    warnings: list[dict[str, str]] = []
    for known_source_id in discover_source_ids(vault):
        page_path = output_dir / f"{known_source_id}_outcome.html"
        if known_source_id != source_id and not page_path.exists():
            warnings.append({
                "source_id": known_source_id,
                "reason": "historical outcome page is missing; excluded from incremental index",
            })
            continue
        try:
            entries.append(outcome_index_entry(known_source_id, vault, page_path))
        except SystemExit as exc:
            if known_source_id == source_id:
                raise
            warnings.append({"source_id": known_source_id, "reason": str(exc)})
    entries = sort_entries_newest_first(entries)
    nebula_report: dict[str, object] = {}
    index_pages = write_paginated_index(entries, output_dir, page_size, vault=vault, nebula_report=nebula_report)
    index_path = index_pages[0] if index_pages else output_dir / "index.html"
    portal.write_text(render_portal_markdown(entries, portal, vault), encoding="utf-8")
    paused = paused_nebula(nebula_report.get("nebula"))
    return {
        "status": "ready",
        "incremental": True,
        "source_id": source_id,
        "source_count": len(entries),
        "page_size": page_size,
        "page_count": max(1, (len(entries) + page_size - 1) // page_size),
        "output_dir": str(output_dir),
        "html_index": str(index_path),
        "index_pages": [str(path) for path in index_pages],
        "obsidian_portal": str(portal),
        "pages": [current_result["output"]],
        "current_page": current_result["output"],
        "completed_refresh": current_result.get("completed_refresh", {}),
        "cleaned_retired_taste_routes": [str(path) for path in cleaned_retired_routes],
        "historical_warnings": warnings,
        "validation_manifest": outcome_manifest(),
        **({"nebula": paused} if paused else {}),
    }






def thinking_needs_review(output_dir: Path) -> bool:
    path = output_dir / 'thinking.html'
    if not path.is_file():
        return True
    match = re.search(r'<script type="application/json" id="catalog-data">(.*?)</script>', path.read_text(encoding='utf-8'), re.S)
    return not match or any(row.get('needs_review') for row in json.loads(match.group(1))['items'])


def paused_nebula(value: object) -> dict[str, object] | None:
    """The answer of ``knowledge_nebula.refresh_installed`` when it paused (unregistered reader code: nothing was rebuilt), else None.

    A pause is always reported in the result of the tool that asked; an ordinary result keeps exactly the fields it always had."""
    return value if isinstance(value, dict) and value.get("status") == "paused" else None


def refresh_outcome_index(*, vault: Path, output_dir: Path = DEFAULT_VAULT_OUTPUT_DIR,
                          portal: Path = DEFAULT_PORTAL,
                           page_size: int = DEFAULT_INDEX_PAGE_SIZE,
                           defer_nebula: bool = False) -> dict[str, object]:
    """Read existing source outcomes; only write catalogues, portal and thinking readers."""
    if page_size < 1:
        raise ValueError("page_size must be greater than 0")
    vault, output_dir, portal = vault.resolve(), output_dir.resolve(), portal.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    portal.parent.mkdir(parents=True, exist_ok=True)
    entries, warnings = [], []
    for source_id in discover_source_ids(vault):
        page_path = output_dir / f"{source_id}_outcome.html"
        if not page_path.is_file():
            warnings.append({"source_id": source_id, "reason": "historical outcome page is missing"})
            continue
        try:
            entries.append(outcome_index_entry(source_id, vault, page_path))
        except (SystemExit, OSError, ValueError) as exc:
            warnings.append({"source_id": source_id, "reason": str(exc)})
    entries = sort_entries_newest_first(entries)
    nebula_report: dict[str, object] = {}
    pages = write_paginated_index(entries, output_dir, page_size, vault=vault,
                                  defer_nebula=defer_nebula, nebula_report=nebula_report)
    portal.write_text(render_portal_markdown(entries, portal, vault), encoding="utf-8")
    result = {"status": "needs_review" if warnings or thinking_needs_review(output_dir) else "ready",
              "thinking_status": "needs_review" if thinking_needs_review(output_dir) else "ready",
              "html_index": str(pages[0]), "source_count": len(entries),
              "index_pages": [str(p) for p in pages], "historical_warnings": warnings,
              "obsidian_portal": str(portal), "historical_pages_rewritten": 0}
    if not defer_nebula:  # the caller that defers the nebula (reader service, release gate, thinking archive) rebuilds it itself
        result["nebula"] = nebula_report.get("nebula") or {"status": "not_installed"}
    return result


def related_source_block(vault, source_id, output_parent):
    """Project explicit saved relationships in both directions; never rewrite notes."""
    links = {}
    for path in sorted((vault / "applications").rglob("APP-*.md")):
        fields = frontmatter_fields(path)
        owner = str(fields.get("source_id") or "")
        try:
            relations = json.loads(str(fields.get("related_sources_json") or "[]"))
        except (ValueError, TypeError):
            continue
        if not isinstance(relations, list):
            continue
        for relation in relations:
            if not isinstance(relation, dict):
                continue
            target = str(relation.get("source_id") or "")
            other = target if owner == source_id else owner if target == source_id else ""
            if not other or other == source_id:
                continue
            try:
                note = resolve_source_note(vault, other)
                metadata, _ = read_markdown_record(note)
            except (SystemExit, OSError, ValueError):
                continue
            links[other] = (str(metadata.get("title") or other), str(relation.get("note") or ""))
    if not links:
        return ""
    rows = []
    for other, (title, note) in sorted(links.items()):
        href = relative_url(vault / "outcome-pages" / (other + "_outcome.html"), output_parent)
        rows.append(f'<li><a href="{escape(href)}">{escape(other)} · {escape(title)}</a><p>{escape(note)}</p></li>')
    return '<section class="related-sources"><h2>相關知識紀錄</h2><ul>' + ''.join(rows) + '</ul></section>'


PRIOR_EFFECT_LABELS = (("supports", "鞏固"), ("contradicts", "反駁"), ("limits", "限制"),
                       ("extends", "延伸"), ("tradeoff", "形成取捨"), ("applies_to", "可應用於"))


def prior_knowledge_effect_block(vault, source_id, output_parent):
    """What this source did to older knowledge, read from the registry only (outcome-page spec, section 2).

    Absent (empty text) when the source has no linked cross-source relation and merged no existing concept,
    so a page of a source that did nothing to older knowledge keeps its bytes.
    """
    labels = dict(PRIOR_EFFECT_LABELS)
    relations_file, kp_file = vault / "_registry" / "relations.jsonl", vault / "_registry" / "knowledge_points.jsonl"
    kp_rows = {str(row.get("kp_uid")): row for row in read_jsonl_rows(kp_file)} if kp_file.is_file() else {}
    linked = lambda uid: uid in kp_rows and str(kp_rows[uid].get("status", "linked")) == "linked"
    links = [row for row in (read_jsonl_rows(relations_file) if relations_file.is_file() else [])
             if row.get("scope") == "cross_source" and row.get("source_id") == source_id
             and str(row.get("status", "linked")) == "linked" and row.get("relation") in labels
             and linked(str(row.get("source_kp_uid"))) and linked(str(row.get("target_kp_uid")))]
    merged = {str(row.get("concept_name")) for uid, row in kp_rows.items()
              if row.get("source_id") == source_id and linked(uid)
              and str(row.get("concept_name") or "").strip() and str(row.get("understanding_delta") or "").strip()}
    if not links and not merged:
        return ""
    order = [relation for relation, _ in PRIOR_EFFECT_LABELS]
    counts = {relation: sum(1 for row in links if row["relation"] == relation) for relation in order}
    summary = "、".join(f"{label} {counts[relation]}" for relation, label in PRIOR_EFFECT_LABELS) + f"、併入既有概念 {len(merged)}。"
    items = []
    for row in sorted(links, key=lambda row: (order.index(str(row["relation"])), str(row.get("target_kp_uid")), str(row.get("relation_id")))):
        other = str(row.get("target_source_id") or "")
        title = other
        try:
            title = str(read_markdown_record(resolve_source_note(vault, other))[0].get("title") or other)
        except (SystemExit, OSError, ValueError):
            pass
        href = relative_url(vault / "outcome-pages" / f"{other}_outcome.html", output_parent)
        items.append(f'<li><strong>{escape(labels[str(row["relation"])])}</strong>：{escape(row.get("insight") or "")}'
                     f'（對方來源：<a href="{escape(href)}">{escape(title)}</a>）</li>')
    return ('<section class="prior-knowledge-effect"><h2>這份來源對舊知識的作用</h2><p>' + escape(summary) + '</p>'
            + (f'<ul>{"".join(items)}</ul>' if items else "") + '</section>')


def generate_outcome_page(source_id: str, vault: Path, output: Path | None = None, *, root: Path = REPO_ROOT) -> dict[str, object]:
    vault = vault.resolve()
    if output is None:
        output = DEFAULT_OUTPUT_DIR / f"{source_id}_outcome.html"
    output = output.resolve()
    root = Path(root).resolve()
    completed_kp = completed_v2_package_for_standard_outcome(vault, source_id, output, root=root)
    if completed_kp is not None:
        try:
            from tools.compiler_worker_v2 import refresh_completed_outcome
        except ModuleNotFoundError:
            from compiler_worker_v2 import refresh_completed_outcome
        refreshed = refresh_completed_outcome(root, completed_kp)
        return {
            "status": str(refreshed.get("status") or "ready"),
            "source_id": source_id,
            "output": str(output),
            "completed_refresh": refreshed,
        }
    source_note = resolve_source_note(vault, source_id)
    app_note = resolve_application_note(vault, source_id, source_note)
    source_fields, source_body = read_markdown_record(source_note)
    source_fields = with_source_citation_evidence(source_fields, source_body)
    source_fields["reader_title"] = reader_title_from_saved_source(source_fields, source_body)
    if str(source_fields.get("asset_mode") or "") == "case_evidence":
        source_fields["_case_method_body"] = section_after_heading(
            section_after_h2(source_body, "案例證據"), "### 方法卡"
        )
    app_fields, app_body = read_markdown_record(app_note)
    source_input_kind, source_identity = validated_source_provenance(source_fields, source_id)
    coverage_status = str(source_fields.get("coverage_audit_status") or "").strip()
    if coverage_status in {"needs_review", "blocked"}:
        raise SystemExit(f"{source_id} coverage audit is not complete: {coverage_status}")

    output.parent.mkdir(parents=True, exist_ok=True)

    kp_dir = vault / "knowledge-points" / "by-source" / source_id
    kp_notes = sorted(kp_dir.glob("*.md")) if kp_dir.exists() else []
    kp_records = [(path, *read_markdown_record(path)) for path in kp_notes]
    try:
        from .obsidian_vault_adapter import CROSS_SOURCE_SECTIONS, resolved_kp_relations
    except ImportError:
        from obsidian_vault_adapter import CROSS_SOURCE_SECTIONS, resolved_kp_relations
    # The reader sees the concept-page section names for the relation vocabulary, from the one table.
    relation_labels = {relation: heading for relation, heading, *_ in CROSS_SOURCE_SECTIONS}
    kp_records = [(path, *resolved_kp_relations(vault, fields, body, relation_labels)) for path, fields, body in kp_records]
    knowledge_points = [fields for _, fields, _ in kp_records]

    asset_mode = str(source_fields.get("asset_mode") or "formal_infographic").strip()
    if asset_mode == "case_evidence":
        asset_rels = list_value(source_fields.get("evidence_screenshots"))
    elif asset_mode == "knowledge_only":
        asset_rels = []
    else:
        asset_rels = list_value(source_fields.get("infographics"))
    if not asset_rels and asset_mode not in {"case_evidence", "knowledge_only"}:
        fallback_asset = str(app_fields.get("PNG") or "").strip()
        asset_rels = [fallback_asset] if fallback_asset else []
    asset_paths = [(vault / asset_rel).resolve() for asset_rel in asset_rels]
    missing_assets = [
        asset_rel
        for asset_rel, asset_path in zip(asset_rels, asset_paths)
        if not asset_path.exists()
    ]
    if asset_mode != "knowledge_only" and (not asset_paths or missing_assets):
        missing = ", ".join(missing_assets) if missing_assets else "<empty>"
        raise SystemExit(f"Missing outcome page asset for {source_id}: {missing}")
    try:
        reproduction_entrypoint = case_evidence_reproduction_entrypoint_path(
            vault,
            source_id,
            source_fields,
        )
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    research_work_issues = research_reuse_work_path_issues(vault, source_id)
    if research_work_issues:
        raise SystemExit("; ".join(research_work_issues))
    method_verification = research_method_verification(vault, source_id)
    supplementary_asset_rels = list_value(source_fields.get("supplementary_assets"))
    supplementary_asset_paths = [(vault / asset_rel).resolve() for asset_rel in supplementary_asset_rels]
    missing_supplementary_assets = [
        asset_rel
        for asset_rel, asset_path in zip(supplementary_asset_rels, supplementary_asset_paths)
        if not asset_path.exists()
    ]
    outside_supplementary_assets = [
        asset_rel
        for asset_rel, asset_path in zip(supplementary_asset_rels, supplementary_asset_paths)
        if asset_path != vault and vault not in asset_path.parents
    ]
    if missing_supplementary_assets or outside_supplementary_assets:
        reasons = []
        if missing_supplementary_assets:
            reasons.append("missing: " + ", ".join(missing_supplementary_assets))
        if outside_supplementary_assets:
            reasons.append("outside vault: " + ", ".join(outside_supplementary_assets))
        raise SystemExit(f"Invalid supplementary outcome page asset for {source_id}: {'; '.join(reasons)}")
    source_original_image_src = ""
    if source_input_kind == "user_provided_image":
        original_rel = str(source_fields.get("source_original_image") or "").strip()
        original_path = (vault / original_rel).resolve() if original_rel else None
        if not original_rel or not original_path or not original_path.exists() or vault not in original_path.parents:
            raise SystemExit(f"Missing retained original image evidence for {source_id}")
        source_original_image_src = relative_url(original_path, output.parent)

    sections = {
        "relationship": section_after_heading(app_body, "### 關係"),
        "insight": section_after_heading(app_body, "### 洞見"),
        "scenes": list_items(section_after_heading(app_body, "### 應用場景")),
        "compounding": section_after_heading(app_body, "## 跨來源洞見判斷"),
    }
    if not sections['insight'] and not reader_layer_sections(app_body).get('複利洞見'):
        # Older saved applications kept the actual reasoning under each named
        # judgment, before the separate reader-summary heading existed.
        # Project those exact saved sentences; never invent or waive a summary.
        legacy_reasons = re.findall(r'(?m)^- (?:補底|加深|連接|修正|行動)[:：][ \t]*成立[；;][ \t]*(\S[^\r\n]*)$', sections['compounding'])
        sections['insight'] = '\n\n'.join(dict.fromkeys(legacy_reasons))
    compounding_status = str(app_fields.get("compounding_insight_status") or "")
    compounding_types = list_value(app_fields.get("compounding_insight_types"))
    compounding_links = list_value(app_fields.get("compounding_insight_links"))
    compounding_issues = compounding_metadata_issues({
        "compounding_insight_status": compounding_status,
        "compounding_insight_types": compounding_types,
        "compounding_insight_links": compounding_links,
        "compounding_next_action": str(app_fields.get("compounding_next_action") or ""),
        "_compounding_section": sections["compounding"],
        "_compounding_reader_summary": reader_layer_sections(app_body).get("複利洞見") or sections["insight"],
    })
    if compounding_issues:
        raise SystemExit(
            f"{source_id} has inconsistent compounding judgment: {', '.join(compounding_issues)}"
        )
    reader_sections = reader_layer_sections(app_body)
    url_overview = (
        reader_sections.get("這條 URL 講甚麼", "")
        or reader_sections.get("這份文字講甚麼", "")
        or str(source_fields.get("summary") or "")
    )
    relationship = reader_sections.get("與你有何關係") or sections["relationship"]
    insight = reader_sections.get("複利洞見") or sections["insight"]
    scenes = list_items(reader_sections.get("可行動驗證問題") or "") or sections["scenes"]
    category_judgments = compounding_categories(sections["compounding"])
    (
        display_app_fields,
        display_knowledge_points,
        url_overview,
        relationship,
        insight,
        scenes,
        category_judgments,
    ) = allocate_outcome_visible_content(
        app_fields=app_fields,
        knowledge_points=knowledge_points,
        url_overview=url_overview,
        relationship=relationship,
        insight=insight,
        scenes=scenes,
        category_judgments=category_judgments,
    )

    page = strip_trailing_whitespace(render_html(
        source_id=source_id,
        source_fields=source_fields,
        app_fields=display_app_fields,
        knowledge_points=display_knowledge_points,
        asset_srcs=[relative_url(asset_path, output.parent) for asset_path in asset_paths],
        supplementary_asset_srcs=[
            relative_url(asset_path, output.parent)
            for asset_path in supplementary_asset_paths
        ],
        source_original_image_src=source_original_image_src,
        case_evidence_reproduction_url=(
            relative_url(reproduction_entrypoint, output.parent)
            if reproduction_entrypoint is not None
            else ""
        ),
        url_overview=url_overview,
        relationship=relationship,
        insight=insight,
        scenes=scenes,
        category_judgments=category_judgments,
        vault_note_sections=stored_note_sections(
            vault=vault,
            source_note=source_note,
            source_body=source_body,
            app_note=app_note,
            app_body=app_body,
            kp_records=kp_records,
            reader_title=str(source_fields.get("reader_title") or ""),
            source_fields=source_fields,
        ),
        source_overview_heading=(
            "這條 URL 講甚麼" if source_input_kind == "url"
            else "這張圖片說甚麼" if source_input_kind == "user_provided_image"
            else "這份文字講甚麼"
        ),
        validated_source_input_kind=source_input_kind,
        validated_source_identity=source_identity,
        research_reuse=research_reuse_for_source(vault, source_id, output.parent),
        method_verification=method_verification,
    ))
    try:
        from .reader_shell import apply_theme, atomic_reader_write, catalogue_projection_lock
    except ImportError:
        from reader_shell import apply_theme, atomic_reader_write, catalogue_projection_lock
    page = page.replace('<section class="vault-body">', prior_knowledge_effect_block(vault, source_id, output.parent)
                        + related_source_block(vault, source_id, output.parent) + '<section class="vault-body">', 1)
    page = apply_theme(page, output, vault, 'knowledge')
    with catalogue_projection_lock(output.parent):
        atomic_reader_write(output, page)

    return {
        "status": "ready",
        "source_id": source_id,
        "output": str(output),
        "source_note": str(source_note),
        "application_note": str(app_note),
        "knowledge_points": len(knowledge_points),
        "asset": str(asset_paths[0]) if asset_paths else "",
        "assets": [str(asset_path) for asset_path in asset_paths],
        "supplementary_assets": [str(asset_path) for asset_path in supplementary_asset_paths],
        "compounding_status": compounding_status,
        "compounding_types": compounding_types,
        "validation_manifest": outcome_manifest(),
    }


def completed_v2_package_for_standard_outcome(vault: Path, source_id: str, output: Path, *, root: Path = REPO_ROOT) -> Path | None:
    """Route a completed v2 source's live page through its receipt-bound refresh only."""
    standard = (vault / "outcome-pages" / f"{source_id}_outcome.html").resolve()
    if output != standard:
        return None
    try:
        from .workspace_paths import WorkspacePathError, bound_vault_path
    except ImportError:
        from workspace_paths import WorkspacePathError, bound_vault_path
    root = Path(root).resolve()
    try:
        bound_vault_path(root, vault)
    except WorkspacePathError as exc:
        raise SystemExit("standard completed outcome cannot verify the workspace-bound vault") from exc
    package_root = root / "outputs" / "compiler_worker_v2"
    if not package_root.is_dir():
        return None
    jobs_root = package_root / "_jobs"
    for job_path in jobs_root.glob("*.json") if jobs_root.is_dir() else []:
        try:
            job = json.loads(job_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if (job.get("schema_version") == "compiler_worker_v2_job_v1"
                and job.get("source_id") == source_id
                and job.get("status") in {"running", "awaiting_imagegen", "committing"}):
            # A new formal projection is still producing its first receipt.
            # It must not be redirected through a previous receipt for this source.
            return None
    try:
        from . import compiler_worker_v2 as worker
    except ImportError:
        import compiler_worker_v2 as worker
    matches: dict[str, Path] = {}
    superseded: set[str] = set()
    found_completed_source = False
    for receipt_path in package_root.rglob("completion_receipt*.json"):
        try:
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if receipt.get("source_id") != source_id:
            continue
        found_completed_source = True
        if receipt.get('schema_version') != worker.COMPLETION_SCHEMA_VERSION:
            continue
        kp_path = worker.relative_to_root(root / str(receipt.get('knowledge_package_path') or ''), root, 'completed Knowledge Package')
        if (receipt.get("status") != "completed" or not kp_path.is_file()
                or receipt.get('payload_sha256') != worker.canonical_payload_sha256(receipt)):
            raise SystemExit(f"{source_id} completed v2 package is incomplete; use the receipt-bound repair entry")
        kp = worker.read_json(kp_path)
        job_path = worker.job_file(root, str(kp.get('job_id') or ''))
        if job_path.is_file():
            job = worker.read_json(job_path)
            named_value = job.get('completion_receipt_path')
            if named_value:
                named = worker.relative_to_root(root / named_value, root, 'named completion')
                if named.parent == receipt_path.parent and named != receipt_path:
                    current = worker.read_json(named)
                    if (current.get('payload_sha256') != worker.canonical_payload_sha256(current)
                            or current.get('source_identity') != receipt.get('source_identity')
                            or current.get('source_id') != source_id
                            or current.get('job_id') != kp.get('job_id')
                            or current.get('schema_version') != worker.COMPLETION_SCHEMA_VERSION
                            or current.get('status') != 'completed'):
                        raise SystemExit(f'{source_id} named completion is not verified')
                    superseded.add(receipt_path.relative_to(root).as_posix())
                    continue
        bound = worker.bound_completion_path(root, receipt_path.parent, kp_path, kp)
        if bound != receipt_path:
            superseded.add(receipt_path.relative_to(root).as_posix())
            continue
        pointer_path = receipt_path.parent / "infographic_current.json"
        if pointer_path.is_file():
            try:
                pointer = worker.read_current_infographic_pointer(root, receipt_path.parent, kp)
            except (OSError, json.JSONDecodeError):
                raise SystemExit(f"{source_id} completed v2 pointer is unreadable; use the receipt-bound repair entry")
            current_receipt = str(pointer.get("current_completion_path") or "")
            if current_receipt and current_receipt != receipt_path.relative_to(root).as_posix():
                continue
        origin_path = receipt_path.parent / 'revision_origin.json'
        if origin_path.is_file():
            origin = worker.read_json(origin_path)
            if origin.get('schema_version') == 'case_revalidation_origin_v1':
                prior = worker.relative_to_root(root / str(origin.get('original_completion_path') or ''), root, 'previous case completion')
                revision = kp.get('revision') or {}
                origin_hash = next((item.get('sha256') for item in receipt.get('artifact_manifest', [])
                                    if item.get('path') == origin_path.relative_to(root).as_posix()), None)
                if (origin.get('payload_sha256') != worker.canonical_payload_sha256(origin)
                        or not origin_hash or not worker.receipt_artifact_matches(origin_path, origin_hash)
                        or not worker.receipt_artifact_matches(kp_path, receipt.get('knowledge_package_sha256'))
                        or revision.get('kind') != 'case_revalidation'
                        or not origin.get('revision_id') or origin.get('revision_id') != revision.get('revision_id')
                        or origin.get('job_id') != receipt.get('job_id')
                        or not origin.get('case_id') or not origin.get('revision_plan_sha256')
                        or origin.get('revision_plan_sha256') != revision.get('revision_plan_sha256')
                        or origin.get('original_completion_path') != revision.get('original_completion_path')
                        or origin.get('original_completion_receipt_sha256') != revision.get('original_completion_receipt_sha256')
                        or origin.get('source_identity') != receipt.get('source_identity')
                        or origin.get('source_id') != source_id
                        or prior.parent == receipt_path.parent
                        or not receipt_path.parent.is_relative_to(prior.parent)
                        or not prior.is_file()
                        or worker.sha256_bytes(prior.read_bytes()) != origin.get('original_completion_receipt_sha256')):
                    raise SystemExit(f'{source_id} case revision origin is not verified')
                superseded.add(prior.relative_to(root).as_posix())
        matches[receipt_path.relative_to(root).as_posix()] = kp_path
    matches = {key:value for key,value in matches.items() if key not in superseded}
    if len(matches) > 1:
        raise SystemExit(f"{source_id} has multiple completed v2 packages; use the receipt-bound repair entry")
    if found_completed_source and not matches:
        raise SystemExit(f'{source_id} has no verified current completion; refuse unguarded regeneration')
    return next(iter(matches.values())) if matches else None


def validated_source_provenance(source_fields: dict[str, object], source_id: str) -> tuple[str, str]:
    source_url = str(source_fields.get("source_url") or "").strip()
    declared_kind = str(source_fields.get("source_input_kind") or "").strip()
    identity = str(source_fields.get("source_identity") or "").strip()
    if not declared_kind:
        if source_url:
            return "url", identity
        raise SystemExit(f"{source_id} is missing source_input_kind and source_url; cannot render a provenance-safe outcome page")
    if declared_kind == "url":
        if not source_url:
            raise SystemExit(f"{source_id} declares URL input but has no source_url")
        return declared_kind, identity
    if declared_kind == "user_provided_text":
        if source_url:
            raise SystemExit(f"{source_id} declares user-provided text but also carries a source_url")
        if not re.fullmatch(r"user_provided_text:[0-9a-f]{64}", identity):
            raise SystemExit(f"{source_id} has invalid user-provided-text source_identity")
        original_url = str(source_fields.get("_original_source_url") or "").strip()
        original_url_status = str(source_fields.get("_original_source_url_status") or "").strip()
        if original_url or original_url_status:
            if not original_url:
                raise SystemExit(f"{source_id} has a citation URL status without an original_source_url")
            if original_url_status != "verified":
                raise SystemExit(f"{source_id} cited original URL is not verified")
            if not original_url.lower().startswith("https://"):
                raise SystemExit(f"{source_id} cited original URL must use HTTPS")
            try:
                normalize_public_url(original_url)
            except ValueError as exc:
                raise SystemExit(f"{source_id} cited original URL is invalid: {exc}") from exc
        return declared_kind, identity
    if declared_kind == "user_provided_image":
        if source_url:
            raise SystemExit(f"{source_id} declares user-provided image but also carries a source_url")
        image_hash = str(source_fields.get("source_image_sha256") or "")
        if not re.fullmatch(r"user_provided_image:[0-9a-f]{64}", identity) or not re.fullmatch(r"[0-9a-f]{64}", image_hash):
            raise SystemExit(f"{source_id} has invalid user-provided-image source identity or hash")
        return declared_kind, identity
    raise SystemExit(f"{source_id} has unsupported source_input_kind: {declared_kind}")


def with_source_citation_evidence(source_fields: dict[str, object], source_body: str) -> dict[str, object]:
    enriched = dict(source_fields)
    citation = source_citation_evidence(source_body)
    enriched.update(citation)
    return enriched


def source_citation_evidence(source_body: str) -> dict[str, str]:
    labels = {
        "_original_title": ("原文題名",),
        "_original_publication": ("原始來源機構", "來源機構"),
        "_original_author": ("原作者", "原始作者"),
        "_original_date": ("原文日期",),
        "_original_source_url": ("原文 URL",),
        "_original_source_url_status": ("原文 URL 核對狀態",),
        "source_label": ("來源標示",),
        "_primary_work_title": ("主要作品標題",),
    }
    evidence: dict[str, str] = {}
    for key, label_options in labels.items():
        for label in label_options:
            value = markdown_table_value(source_body, label)
            if value:
                evidence[key] = value
                break
    return evidence


def markdown_table_value(markdown: str, label: str) -> str:
    pattern = re.compile(
        r"^\|\s*" + re.escape(label) + r"\s*\|\s*(.*?)\s*\|\s*$",
        re.MULTILINE,
    )
    match = pattern.search(markdown)
    if not match:
        return ""
    value = re.sub(r"<[^>]+>", "", match.group(1)).strip()
    return value


def outcome_manifest() -> dict[str, object]:
    try:
        return build_embedded_manifest("outcome-page", root=REPO_ROOT)
    except Exception as exc:  # pragma: no cover - defensive reporting for CLI surface
        return {
            "status": "needs_review",
            "scenario": "outcome-page",
            "blocked_reasons": [{"reason": "manifest_build_failed", "message": str(exc)}],
        }


def outcome_index_entry(source_id: str, vault: Path, page_path: Path) -> dict[str, object]:
    source_note = resolve_source_note(vault, source_id)
    app_note = resolve_application_note(vault, source_id, source_note)
    source_fields, source_body = read_markdown_record(source_note)
    source_fields = with_source_citation_evidence(source_fields, source_body)
    app_fields, _ = read_markdown_record(app_note)
    asset_mode = str(source_fields.get("asset_mode") or "formal_infographic")
    asset_rel = (
        first_list_value(source_fields.get("evidence_screenshots"))
        if asset_mode == "case_evidence"
        else first_list_value(source_fields.get("infographics")) or str(app_fields.get("PNG") or "")
    )
    asset_path = (vault / asset_rel).resolve() if asset_rel else None
    direct_source_url = str(source_fields.get("source_url") or "")
    original_source_url = str(source_fields.get("_original_source_url") or "")
    return {
        "source_id": source_id,
        "title": reader_title_from_saved_source(source_fields, source_body),
        "summary": str(source_fields.get("summary") or ""),
        "source_url": direct_source_url or original_source_url,
        "source_url_label": "打開原文 URL" if original_source_url and not direct_source_url else "打開原 URL",
        "source_input_kind": str(source_fields.get("source_input_kind") or "url"),
        "source_identity": str(source_fields.get("source_identity") or ""),
        "sort_date": first_sort_date(source_fields, app_fields),
        "adoption": str(app_fields.get("AI總判斷") or app_fields.get("adoption_status") or ""),
        "confidence": str(app_fields.get("AI信心") or app_fields.get("ai_review_confidence") or ""),
        "taste": str(app_fields.get("品味") or app_fields.get("taste_network_status") or ""),
        "compounding": display_status(str(app_fields.get("複利") or app_fields.get("compounding_insight_status") or "")),
        "page_path": page_path.resolve(),
        "source_note": source_note.resolve(),
        "application_note": app_note.resolve(),
        "asset_path": asset_path,
        "knowledge_points": (vault / "knowledge-points" / "by-source" / source_id).resolve(),
    }


def sort_entries_newest_first(entries: list[dict[str, object]]) -> list[dict[str, object]]:
    return sorted(entries, key=outcome_sort_key, reverse=True)


def outcome_sort_key(entry: dict[str, object]) -> tuple[int, int, str]:
    source_id = str(entry.get("source_id") or "")
    return (
        sortable_date_number(str(entry.get("sort_date") or "")),
        source_id_number(source_id),
        source_id,
    )


def first_sort_date(*field_groups: dict[str, object]) -> str:
    for key in ("capture_date", "created", "date", "updated", "generated_at"):
        for fields in field_groups:
            value = str(fields.get(key) or "").strip()
            if value:
                return value
    return ""


def sortable_date_number(value: str) -> int:
    if not value:
        return 0
    normalized = value.strip().replace("Z", "+00:00")
    try:
        return int(datetime.fromisoformat(normalized).strftime("%Y%m%d%H%M%S"))
    except ValueError:
        match = re.search(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})", normalized)
        if match:
            year, month, day = match.groups()
            return int(f"{int(year):04d}{int(month):02d}{int(day):02d}000000")
    return 0


def source_id_number(source_id: str) -> int:
    match = re.search(r"(\d+)(?!.*\d)", source_id)
    return int(match.group(1)) if match else 0


def single_match(root: Path, pattern: str, label: str) -> Path:
    matches = sorted(root.glob(f"**/{pattern}"))
    if not matches:
        raise SystemExit(f"Missing {label}: {pattern}")
    if len(matches) > 1:
        raise SystemExit(f"Ambiguous {label}: {pattern}")
    return matches[0]


def resolve_source_note(vault: Path, source_id: str) -> Path:
    registered = registered_source_note(vault, source_id)
    if registered:
        return registered
    return single_match(vault / "sources", f"{source_id}_*.md", "source note")


def registered_source_note(vault: Path, source_id: str) -> Path | None:
    registry = vault / "_registry" / "sources.jsonl"
    if not registry.exists():
        return None
    for row in read_jsonl_rows(registry):
        if str(row.get("source_id") or "").strip() != source_id:
            continue
        relative = str(row.get("source_note_path") or "").strip()
        if not relative:
            return None
        candidate = (vault / relative).resolve()
        if not candidate.exists():
            raise SystemExit(f"Registered source note is missing for {source_id}: {relative}")
        if candidate == vault or vault not in candidate.parents:
            raise SystemExit(f"Registered source note is outside vault for {source_id}: {relative}")
        return candidate
    return None


def resolve_application_note(vault: Path, source_id: str, source_note: Path) -> Path:
    source_slug = source_note_slug(source_id, source_note)
    if source_slug:
        source_relative = source_note.resolve().relative_to(vault.resolve())
        if len(source_relative.parts) >= 4 and source_relative.parts[0] == "sources":
            canonical = vault / "applications" / source_relative.parts[1] / source_relative.parts[2] / f"APP-{source_id}_{source_slug}.md"
            if canonical.exists():
                return canonical
        exact_matches = sorted((vault / "applications").glob(f"**/APP-{source_id}_{source_slug}.md"))
        if len(exact_matches) == 1:
            return exact_matches[0]
        if len(exact_matches) > 1:
            raise SystemExit(f"Ambiguous canonical application note: APP-{source_id}_{source_slug}.md")
    return single_match(vault / "applications", f"APP-{source_id}_*.md", "application note")


def source_note_slug(source_id: str, source_note: Path) -> str:
    prefix = f"{source_id}_"
    stem = source_note.stem
    if stem.startswith(prefix):
        return stem[len(prefix):]
    return ""


def read_jsonl_rows(path: Path) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise SystemExit(f"Invalid JSONL in {path} line {line_number}: {exc}") from exc
        if isinstance(value, dict):
            rows.append(value)
    return rows


def read_markdown_record(path: Path) -> tuple[dict[str, object], str]:
    text = path.read_text(encoding="utf-8-sig")
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end == -1:
        return {}, text
    frontmatter = text[3:end].strip("\n")
    body = text[end + len("\n---"):].lstrip()
    return parse_frontmatter(frontmatter), body


def parse_frontmatter(text: str) -> dict[str, object]:
    result: dict[str, object] = {}
    current_key: str | None = None
    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        if not line:
            continue
        if line.startswith("  - ") and current_key:
            value = clean_value(line[4:])
            existing = result.setdefault(current_key, [])
            if isinstance(existing, list):
                existing.append(value)
            continue
        if ":" not in line or line.startswith(" "):
            continue
        key, value = line.split(":", 1)
        key = key.strip()
        value = value.strip()
        current_key = key
        result[key] = [] if value in ("", "[]") else clean_value(value)
    return result


def clean_value(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] == '"':
        return value[1:-1]
    return value


def list_value(value: object) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip() and value.strip() != "[]":
        return [value.strip()]
    return []


def first_list_value(value: object) -> str:
    items = list_value(value)
    return items[0] if items else ""


def section_after_heading(text: str, heading: str) -> str:
    escaped = re.escape(heading)
    marker = r"(?m)^" + escaped + r"\s*$"
    match = re.search(marker, text)
    if not match:
        return ""
    start = match.end()
    next_heading = re.search(r"(?m)^#{2,3}\s+", text[start:])
    end = start + next_heading.start() if next_heading else len(text)
    return text[start:end].strip()


def reader_layer_sections(text: str) -> dict[str, str]:
    parent = section_after_h2(text, "讀取層成果頁內容")
    if not parent:
        return {}
    sections: dict[str, str] = {}
    matches = list(re.finditer(r"(?m)^###\s+(.+?)\s*$", parent))
    for index, match in enumerate(matches):
        title = match.group(1).strip()
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(parent)
        sections[title] = parent[start:end].strip()
    return sections


def section_after_h2(text: str, heading: str) -> str:
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
                if re.fullmatch(r"##\s+" + re.escape(heading) + r"\s*", line):
                    active = True
                continue
        if active:
            collected.append(line)
    return "\n".join(collected).strip()


def list_items(text: str) -> list[str]:
    items: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if re.match(r"^[-*]\s+", stripped):
            items.append(re.sub(r"^[-*]\s+", "", stripped).strip())
        elif re.match(r"^\d+\.\s+", stripped):
            items.append(re.sub(r"^\d+\.\s+", "", stripped).strip())
    return items


def compounding_categories(section: str) -> dict[str, str]:
    categories = {}
    for label in ("補底", "加深", "連接", "修正", "行動"):
        match = re.search(rf"(?m)^-\s*{label}[:：](.+)$", section)
        categories[label] = match.group(1).strip() if match else "未記錄"
    return categories


class VisibleContentLedger:
    def __init__(self) -> None:
        self._seen: list[str] = []

    def keep_markdown(self, text: str) -> str:
        kept: list[str] = []
        for block in markdown_visible_blocks(text):
            if self.keep_text(block):
                kept.append(block)
        return "\n\n".join(kept)

    def keep_items(self, items: list[str]) -> list[str]:
        return [item for item in items if self.keep_text(item)]

    def keep_text(self, text: object) -> bool:
        normalized = normalized_visible_text(str(text))
        if not normalized:
            return False
        if self.is_redundant(normalized):
            return False
        self.remember(str(text))
        return True

    def remember(self, text: str) -> None:
        for fragment in visible_content_fragments(text):
            normalized = normalized_visible_text(fragment)
            if len(normalized) >= 30 and normalized not in self._seen:
                self._seen.append(normalized)

    def is_redundant(self, normalized: str) -> bool:
        if len(normalized) < 30:
            return False
        for seen in self._seen:
            if normalized == seen:
                return True
            if len(normalized) >= 30 and len(seen) >= 30 and (normalized in seen or seen in normalized):
                return True
        return False


def allocate_outcome_visible_content(
    *,
    app_fields: dict[str, object],
    knowledge_points: list[dict[str, object]],
    url_overview: str,
    relationship: str,
    insight: str,
    scenes: list[str],
    category_judgments: dict[str, str],
) -> tuple[dict[str, object], list[dict[str, object]], str, str, str, list[str], dict[str, str]]:
    ledger = VisibleContentLedger()
    display_app_fields = dict(app_fields)

    url_overview = ledger.keep_markdown(url_overview)
    relationship = ledger.keep_markdown(relationship)
    insight = ledger.keep_markdown(insight)

    # Action questions are reader-facing outcomes, so protect them before lower-level category/link echoes.
    scenes = ledger.keep_items(scenes)
    category_judgments = {
        label: text
        for label, text in category_judgments.items()
        if ledger.keep_text(text)
    }
    for field in ("compounding_insight_links", "taste_links", "personalization_links"):
        display_app_fields[field] = ledger.keep_items(list_value(display_app_fields.get(field)))

    display_knowledge_points: list[dict[str, object]] = []
    for point in knowledge_points:
        display_point = dict(point)
        anchor = str(display_point.get("source_anchor") or "")
        if anchor and not ledger.keep_text(anchor):
            display_point["source_anchor"] = ""
        display_knowledge_points.append(display_point)

    return (
        display_app_fields,
        display_knowledge_points,
        url_overview,
        relationship,
        insight,
        scenes,
        category_judgments,
    )


def markdown_visible_blocks(text: str) -> list[str]:
    blocks: list[str] = []
    for part in re.split(r"\n\s*\n", str(text).strip()):
        stripped = part.strip()
        if not stripped:
            continue
        lines = [line.strip() for line in stripped.splitlines() if line.strip()]
        blocks.extend(lines if len(lines) > 1 else [stripped])
    return blocks


def visible_content_fragments(text: str) -> list[str]:
    fragments = markdown_visible_blocks(text)
    for line in str(text).splitlines():
        cleaned = re.sub(r"^[-*]\s+", "", line.strip())
        cleaned = re.sub(r"^\d+\.\s+", "", cleaned)
        if cleaned:
            fragments.append(cleaned)
    return fragments


def stored_note_sections(
    *,
    vault: Path,
    source_note: Path,
    source_body: str,
    app_note: Path,
    app_body: str,
    kp_records: list[tuple[Path, dict[str, object], str]],
    reader_title: str,
    source_fields: dict[str, object] | None = None,
) -> list[dict[str, str]]:
    archive_mode = str((source_fields or {}).get("source_retention_mode") or "") == TRANSCRIPT_ARCHIVE_MODE
    safe_source_body = (
        transcript_archive_reader_body(source_fields or {}, source_body)
        if archive_mode else reader_safe_source_body(source_body)
    )
    safe_app_body = reader_safe_application_body(app_body, reader_title)
    sections = [
        {
            "title": "📺 影片與字幕存檔" if archive_mode else "來源筆記正文",
            "path": vault_relative(source_note, vault),
            "body": safe_source_body,
        },
        {
            "title": "個人應用正文",
            "path": vault_relative(app_note, vault),
            "body": safe_app_body,
        },
    ]
    for index, (path, fields, body) in enumerate(kp_records, start=1):
        title = str(fields.get("knowledge_point") or fields.get("definition") or fields.get("kp_id") or f"知識點 {index}")
        safe_body = reader_safe_note_body(body)
        if safe_body:
            sections.append({
                "title": f"知識點 {index}: {title}",
                "path": vault_relative(path, vault),
                "body": safe_body,
            })
    return sections


def transcript_archive_reader_body(source_fields: dict[str, object], source_body: str) -> str:
    """Reader section for a YouTube caption archive source.

    The note holds no transcript, so the whole source note is never rendered as
    the "stored body".  The reader gets the video link and where the full
    transcript is kept; the knowledge points show their own short quotations.
    """
    declared: dict[str, str] = {}
    for line in section_after_h2(source_body, "字幕存檔").splitlines():
        match = re.match(r"^\|\s*([^|]+?)\s*\|\s*(.*?)\s*\|\s*$", line)
        if match:
            declared[match.group(1)] = match.group(2)
    url = str(source_fields.get("source_url") or declared.get("影片 URL") or "").strip()
    language = declared.get("字幕語言", "")
    track = declared.get("字幕軌道", "")
    lines = ["# 📺 影片與字幕存檔", ""]
    if url:
        lines.append(f"- 影片連結：[{url}]({url})")
    if language or track:
        lines.append("- 字幕語言：" + "／".join(part for part in (language, track) if part))
    lines.extend([
        "- 完整字幕只保存在工作包存檔，不放入知識庫；知識點的引述可由存檔重建核對。",
        "- 各知識點下方的短引述附有影片時間點連結，可直接跳到對應片段。",
    ])
    return "\n".join(lines)


def reader_safe_source_body(source_body: str) -> str:
    original_body = retained_note_body_section(source_body)
    if original_body:
        # The retained source is data, including quoted internal-looking words.
        # Escape it during rendering; never censor or truncate the full body.
        canonical_heading = re.search(r"(?m)^### Canonical article body\s*$", original_body)
        if canonical_heading:
            return "# 原文正文\n\n" + original_body[canonical_heading.end():].strip()
        return reader_safe_note_body("# 原文正文\n\n" + original_body)
    return reader_safe_note_body(source_body)


def reader_safe_note_body(body: str) -> str:
    safe_lines: list[str] = []
    skipping_table = False
    for raw_line in str(body).splitlines():
        line = raw_line.rstrip()
        stripped = line.strip()
        if is_markdown_table_line(stripped) and has_reader_surface_internal_message(stripped):
            skipping_table = True
            continue
        if skipping_table and is_markdown_table_line(stripped):
            continue
        skipping_table = False
        if has_reader_surface_internal_message(stripped):
            continue
        safe_lines.append(line)
    return "\n".join(safe_lines).strip()


def reader_safe_application_body(app_body: str, reader_title: str) -> str:
    """Project only the legacy escaped-table artifact out of an application heading."""
    safe_body = reader_safe_note_body(app_body)
    source_work_title = reader_title.rsplit(" | ", 1)[0].strip()
    if not source_work_title:
        return safe_body
    legacy_heading = re.compile(
        rf"(?m)^#\s+{re.escape(source_work_title)}\s*\\+\s*—\s*個人應用\s*$"
    )
    return legacy_heading.sub(f"# {source_work_title} — 個人應用", safe_body)


def vault_relative(path: Path, vault: Path) -> str:
    try:
        return str(path.resolve().relative_to(vault.resolve())).replace("\\", "/")
    except ValueError:
        return str(path)


def render_html(
    *,
    source_id: str,
    source_fields: dict[str, object],
    app_fields: dict[str, object],
    knowledge_points: list[dict[str, object]],
    asset_srcs: list[str],
    supplementary_asset_srcs: list[str],
    source_original_image_src: str,
    case_evidence_reproduction_url: str,
    url_overview: str,
    relationship: str,
    insight: str,
    scenes: list[str],
    category_judgments: dict[str, str],
    vault_note_sections: list[dict[str, str]],
    source_overview_heading: str,
    validated_source_input_kind: str,
    validated_source_identity: str,
    research_reuse: list[dict[str, object]],
    method_verification: dict[str, str],
) -> str:
    title = str(source_fields.get("reader_title") or source_fields.get("title") or source_id)
    display_title = display_page_title(title)
    summary = str(source_fields.get("summary") or "")
    overview = url_overview or summary
    source_url = str(source_fields.get("source_url") or "")
    original_source_url = str(source_fields.get("_original_source_url") or "")
    original_title = reader_safe_source_value(source_fields.get("_original_title"), fallback="")
    original_publication, original_author, original_date = reader_source_attribution_details(source_fields)
    source_attribution_label = reader_source_attribution_label(source_fields, fallback="")
    source_input_kind = validated_source_input_kind
    source_identity = validated_source_identity
    asset_mode = str(source_fields.get("asset_mode") or "formal_infographic").strip()
    adoption = str(app_fields.get("AI總判斷") or app_fields.get("adoption_status") or "")
    confidence = str(app_fields.get("AI信心") or app_fields.get("ai_review_confidence") or "")
    taste = str(app_fields.get("品味") or app_fields.get("taste_network_status") or "")
    compounding = display_status(str(app_fields.get("複利") or app_fields.get("compounding_insight_status") or ""))
    compounding_types = list_value(app_fields.get("compounding_insight_types"))
    compounding_links = list_value(app_fields.get("compounding_insight_links"))
    taste_links = list_value(app_fields.get("taste_links"))
    personalization_links = list_value(app_fields.get("personalization_links"))
    generated_at = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %z")
    original_parts = [
        part
        for part in (
            f"《{original_title}》" if original_title else "",
            original_publication,
            original_author,
            original_date,
        )
        if part
    ]
    original_detail = reader_safe_source_value(" / ".join(original_parts), fallback="")
    # A lone author is attribution, not a full citation.  Preserve a richer
    # reader label such as "Erin Higgins／IES" instead of collapsing it to a name.
    if (
        original_detail == original_author
        and source_attribution_label
        and source_attribution_label != original_detail
    ):
        original_detail = ""
    work_title = reader_safe_source_value(
        source_fields.get("_primary_work_title") or source_fields.get("primary_work_title"),
        fallback="",
    )
    if not work_title:
        work_title = reader_safe_source_value(title, fallback="")
    work_title = work_title.strip().removeprefix("《").removesuffix("》").strip()  # stored titles may already carry 《》
    work_title = reader_safe_source_value(f"《{work_title}》", fallback="") if work_title else ""
    reader_source = original_detail or source_attribution_label or work_title
    commentary_source = source_input_kind == 'user_provided_text' and source_fields.get('source_type') == '使用者評註'
    copied_text_detail = '（保存的是評註，並非該文章全文）' if commentary_source else ''
    if source_url:
        source_provenance = (
            '<p class="source-link"><span>原始 URL</span>'
            f'<a href="{escape(source_url)}" target="_blank" rel="noreferrer">{escape(source_url)}</a></p>'
        )
    elif original_source_url:
        source_provenance = (
            '<p class="source-link source-original"><span>原文 URL</span>'
            f'<a href="{escape(original_source_url)}" target="_blank" rel="noreferrer">{escape(original_source_url)}</a></p>'
            '<p class="source-link source-provenance"><span>正式來源</span>'
            f'{escape(reader_source)}{copied_text_detail}</p>'
        )
        if commentary_source:
            source_provenance = source_provenance.replace('<span>正式來源</span>', '<span>評註引用來源</span>')
    else:
        source_provenance = (
            '<p class="source-link source-provenance"><span>正式來源</span>'
            f'{escape(reader_source)}{copied_text_detail}</p>'
        )

    footer_copy = (
        "本頁由 Obsidian 內的原文、應用筆記與知識點重建；只作閱讀入口，不取代正式筆記。"
        if asset_mode == "knowledge_only"
        else "本頁由 Obsidian 內的原文、應用筆記、知識點與資訊圖重建；只作閱讀入口，不取代正式筆記。"
    )
    media_html = outcome_media_block(asset_srcs, supplementary_asset_srcs, title, asset_mode)
    top_class = "grid top" if media_html else "grid top top-no-media"
    infographic_action = optional_infographic_action(source_id, asset_mode)

    return f"""<!doctype html>
<html lang="zh-Hant">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape(title)} — 成果頁</title>
  <style>
    :root {{
      --ink: #202124;
      --muted: #656b73;
      --line: #d8dde3;
      --paper: #f6f7f4;
      --panel: #ffffff;
      --wash: #eef3f2;
      --green: #176b52;
      --orange: #b85c16;
      --blue: #1f6f94;
      --violet: #6750a4;
      --soft: #eef2f6;
      --shadow: 0 18px 45px rgba(32, 33, 36, 0.08);
    }}
    * {{ box-sizing: border-box; }}
    html, body {{ max-width: 100%; overflow-x: hidden; }}
    body {{
      margin: 0;
      background: var(--paper);
      color: var(--ink);
      font-family: "Noto Sans TC", "Microsoft JhengHei", Arial, sans-serif;
      line-height: 1.65;
    }}
    main {{ width: min(1180px, calc(100% - 32px)); margin: 0 auto; padding: 28px 0 48px; }}
    header {{ border-bottom: 1px solid var(--line); padding-bottom: 18px; margin-bottom: 22px; }}
    .kicker {{ color: var(--green); font-weight: 700; letter-spacing: 0; margin-bottom: 6px; }}
    h1 {{ font-size: clamp(30px, 5vw, 58px); line-height: 1.12; margin: 0 0 12px; letter-spacing: 0; overflow-wrap: anywhere; word-break: normal; }}
    h2 {{ font-size: 24px; margin: 0 0 12px; letter-spacing: 0; }}
    h3 {{ font-size: 18px; margin: 0 0 8px; letter-spacing: 0; }}
    p {{ margin: 0 0 12px; overflow-wrap: anywhere; }}
    .summary {{ max-width: 850px; color: #34363a; font-size: 18px; overflow-wrap: anywhere; }}
    .source-link {{ display: flex; flex-wrap: wrap; gap: 8px; align-items: baseline; margin: 12px 0 0; color: var(--muted); font-size: 14px; min-width: 0; }}
    .source-link span {{ color: var(--green); font-weight: 700; }}
    .source-link a {{ color: var(--blue); text-decoration-thickness: 1px; text-underline-offset: 3px; overflow-wrap: anywhere; word-break: break-all; max-width: 100%; min-width: 0; flex: 1 1 220px; }}
    .source-link a:hover {{ color: var(--orange); }}
    .page-nav {{ display:flex; flex-wrap:wrap; gap:8px; margin-bottom: 14px; }}
    .page-nav a {{ display: inline-flex; align-items:center; border: 1px solid var(--line); border-radius: 999px; padding: 7px 11px; background: var(--panel); color: var(--blue); text-decoration: none; }}
    .page-nav a[aria-current="page"] {{ background:var(--green); border-color:var(--green); color:#fff; }}
    .page-nav a:hover {{ color: var(--orange); border-color: var(--orange); }}
    .research-work-actions {{ display: flex; flex-wrap: wrap; gap: 8px; }}
    .research-work-link {{ display: inline-flex; align-items: center; border: 1px solid var(--violet); border-radius: 999px; padding: 7px 11px; background: #f7f3ff; color: var(--violet); font-weight: 700; text-decoration: none; }}
    .research-work-link:hover {{ color: var(--orange); border-color: var(--orange); }}
    .generated-at {{ margin: 4px 0 0; color: var(--muted); font-size: 14px; }}
    .generated-at span {{ color: var(--green); font-weight: 700; }}
    .badges {{ display: flex; flex-wrap: wrap; gap: 8px; margin-top: 14px; }}
    .badge {{ border: 1px solid var(--line); background: var(--panel); border-radius: 8px; padding: 6px 10px; font-size: 14px; }}
    .badge strong {{ color: var(--orange); }}
    .grid {{ display: grid; gap: 18px; }}
    .grid > * {{ min-width: 0; }}
    .top {{ grid-template-columns: minmax(260px, 0.92fr) minmax(320px, 1.08fr); align-items: start; }}
    .top-no-media {{ grid-template-columns: minmax(0, 1fr); }}
    .media-stack {{ display: grid; gap: 12px; min-width: 0; }}
    section {{ margin: 18px 0; }}
    .panel {{ background: var(--panel); border: 1px solid var(--line); border-radius: 8px; padding: 18px; overflow-wrap: anywhere; }}
    .panel.emphasis {{ border-top: 4px solid var(--blue); box-shadow: var(--shadow); }}
    .image-gallery {{ display: grid; gap: 12px; min-width: 0; }}
    .image-wrap {{ min-width: 0; overflow: hidden; margin: 0; }}
    .image-wrap img {{ width: 100%; height: auto; display: block; border: 1px solid var(--line); border-radius: 8px; background: white; }}
    .image-wrap figcaption {{ margin-top: 6px; color: var(--muted); font-size: 13px; }}
    .metric-row {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 10px; margin-top: 12px; }}
    .metric {{ background: var(--soft); border: 1px solid var(--line); border-radius: 8px; padding: 10px; min-height: 82px; }}
    .metric span {{ display: block; color: var(--muted); font-size: 13px; }}
    .metric b {{ display: block; color: var(--ink); font-size: 20px; margin-top: 4px; }}
    .two {{ grid-template-columns: 1fr 1fr; }}
    .three {{ grid-template-columns: repeat(3, 1fr); }}
    .five {{ grid-template-columns: repeat(5, 1fr); }}
    .category {{ border-top: 4px solid var(--green); }}
    .link-group {{ border-top: 4px solid var(--violet); }}
    ul {{ margin: 8px 0 0; padding-left: 20px; }}
    li {{ margin: 5px 0; }}
    h2, h3, strong, li {{ overflow-wrap: anywhere; }}
    .knowledge-section {{ margin-top: 22px; }}
    .knowledge-section > h2 {{ display: flex; align-items: center; gap: 8px; margin-bottom: 10px; }}
    .kp-list {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; align-items: start; }}
    .kp {{
      position: relative;
      border-left: 0;
      padding: 16px 16px 15px 18px;
      background: linear-gradient(180deg, #ffffff 0%, #fbfcfa 100%);
    }}
    .kp::before {{
      content: "";
      position: absolute;
      inset: 14px auto 14px 0;
      width: 4px;
      border-radius: 0 4px 4px 0;
      background: var(--blue);
    }}
    .kp h3 {{ font-size: 17px; line-height: 1.45; margin-bottom: 10px; }}
    .kp-meta {{ display: inline-flex; flex-wrap: wrap; gap: 6px; align-items: center; margin-bottom: 10px; }}
    .kp-meta span {{ display: inline-flex; align-items: center; border: 1px solid var(--line); border-radius: 999px; padding: 3px 8px; background: var(--soft); }}
    .kp-evidence {{ margin: 0; }}
    .kp-evidence > summary {{ cursor: pointer; color: #3d4248; font-weight: 600; }}
    .kp-evidence .kp-anchor {{ margin: 10px 0 0; padding-top: 10px; border-top: 1px solid var(--line); color: #3d4248; }}
    .small {{ color: var(--muted); font-size: 14px; }}
    .action {{ border-left: 5px solid var(--orange); background: #fff8f1; }}
    .optional-infographic-action {{ display:flex; flex-wrap:wrap; align-items:center; gap:10px; margin:0 0 20px; color:var(--muted); font-size:14px; }}
    .optional-infographic-action p {{ margin:0; }}
    .optional-infographic-action .copy {{ border:1px solid var(--line); border-radius:999px; padding:8px 12px; background:var(--panel); color:var(--blue); cursor:pointer; font:inherit; font-weight:700; }}
    .optional-infographic-action .copy:hover {{ color:var(--orange); border-color:var(--orange); }}
    .optional-infographic-action .copy-source {{ position:absolute; width:1px; height:1px; padding:0; border:0; opacity:0; pointer-events:none; }}
    .optional-infographic-action .copy-source.is-visible {{ position:static; flex:1 1 100%; width:100%; height:auto; min-height:42px; padding:8px; border:1px solid var(--line); border-radius:8px; opacity:1; pointer-events:auto; background:var(--panel); color:var(--ink); font:14px/1.5 Consolas, "Noto Sans Mono", monospace; }}
    .optional-infographic-action .status-line {{ min-height:1.5em; color:var(--muted); }}
    .source-evidence img {{ width: 100%; max-height: 460px; object-fit: contain; background: var(--soft); border-radius: 8px; }}
    .vault-body {{ margin-top: 26px; }}
    .vault-body .body-intro {{ color: var(--muted); font-size: 14px; max-width: 760px; }}
    .vault-body details {{ background: var(--panel); border: 1px solid var(--line); border-radius: 8px; margin: 10px 0; overflow: hidden; }}
    .vault-body details[open] {{ box-shadow: var(--shadow); }}
    .vault-body summary {{
      cursor: pointer;
      display: flex;
      justify-content: space-between;
      gap: 12px;
      align-items: center;
      padding: 13px 15px;
      font-weight: 700;
      color: var(--green);
      list-style: none;
    }}
    .vault-body summary::-webkit-details-marker {{ display: none; }}
    .vault-body summary::after {{
      content: "展開";
      flex: 0 0 auto;
      border: 1px solid var(--line);
      border-radius: 999px;
      padding: 2px 8px;
      color: var(--muted);
      font-size: 12px;
      font-weight: 600;
      background: var(--soft);
    }}
    .vault-body details[open] summary::after {{ content: "收合"; color: var(--green); background: #edf6f2; }}
    .vault-body .note-meta {{ margin: 0; padding: 0 15px 10px; color: var(--muted); font-size: 13px; }}
    .note-shell {{ border-top: 1px solid var(--line); padding: 0 15px 16px; background: #fffdf8; }}
    .note-rendered {{ max-height: 68vh; overflow: auto; padding: 14px 2px 0; font-size: 15px; line-height: 1.75; }}
    .case-method-learning .note-rendered {{ max-height: none; overflow: visible; padding-top: 0; }}
    .note-rendered h3, .note-rendered h4, .note-rendered h5, .note-rendered h6 {{ color: var(--ink); margin: 18px 0 8px; line-height: 1.35; }}
    .note-rendered h3 {{ font-size: 20px; }}
    .note-rendered h4 {{ font-size: 17px; }}
    .note-rendered h5, .note-rendered h6 {{ font-size: 15px; }}
    .note-rendered p {{ margin: 0 0 10px; }}
    .note-rendered ul, .note-rendered ol {{ margin: 8px 0 12px; padding-left: 22px; }}
    .note-rendered li {{ margin: 4px 0; }}
    .note-table-wrap {{ overflow-x: auto; margin: 12px 0 16px; border: 1px solid var(--line); border-radius: 8px; background: white; }}
    .note-table {{ width: 100%; border-collapse: collapse; min-width: 520px; font-size: 14px; }}
    .note-table th, .note-table td {{ border-bottom: 1px solid var(--line); padding: 8px 10px; text-align: left; vertical-align: top; }}
    .note-table th {{ background: var(--soft); color: var(--green); font-weight: 700; }}
    .note-table tr:last-child td {{ border-bottom: 0; }}
    .note-code {{ margin: 10px 0 14px; padding: 12px; overflow-x: auto; background: #f3f5f7; border: 1px solid var(--line); border-radius: 8px; font: 13px/1.6 Consolas, "Noto Sans Mono", monospace; }}
    .note-rendered code, .note-ref {{ border: 1px solid var(--line); border-radius: 6px; background: var(--soft); padding: 1px 5px; font: 13px/1.4 Consolas, "Noto Sans Mono", monospace; }}
    .note-rendered blockquote {{ margin: 10px 0 14px; padding: 8px 12px; border-left: 4px solid var(--green); background: #f4faf7; color: #3d4248; }}
    footer {{ margin-top: 26px; color: var(--muted); font-size: 13px; border-top: 1px solid var(--line); padding-top: 14px; }}
    @media (max-width: 860px) {{
      main {{ width: 350px; max-width: 100%; padding: 18px 18px 48px; margin-left: 0; margin-right: auto; }}
      .top, .two, .three, .five, .kp-list, .metric-row {{ grid-template-columns: 1fr; }}
      h1 {{ font-size: 31px; }}
      .summary, .panel p, .panel li, .metric b {{ word-break: break-all; }}
      .badges {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); }}
      .badge {{ width: 100%; min-width: 0; overflow-wrap: anywhere; }}
    }}
  </style>
</head>
<body data-source-input-kind="{escape(source_input_kind)}" data-source-identity="{escape(source_identity)}">
<main>
  <nav class="page-nav" aria-label="系統導航">
    <a href="index.html" aria-current="page">知識成果</a>
    {research_reuse_navigation(research_reuse)}
  </nav>
  <header>
    <div class="kicker">知識成果頁 / {escape(source_id)}</div>
    <h1 title="{escape(title)}">{breakable_text(display_title)}</h1>
    {source_provenance}
    <p class="generated-at"><span>生成時間</span> {escape(generated_at)}</p>
    <div class="badges">
      <span class="badge">AI 判斷：<strong>{escape(adoption)}</strong></span>
      <span class="badge">信心：<strong>{escape(confidence)}</strong></span>
      <span class="badge">品味：<strong>{escape(taste)}</strong></span>
      <span class="badge">複利：<strong>{escape(compounding)}</strong></span>
    </div>
  </header>

  <section class="{top_class}">
    {media_html}
    <div class="grid">
      <div class="panel emphasis">
        <h2>🔎 {escape(source_overview_heading)}</h2>
        <div class="summary">{paragraphs(overview)}</div>
      </div>
      <div class="panel">
        <h2>🧭 與你有何關係</h2>
        {relationship_block(relationship, app_fields, source_fields)}
      </div>
    </div>
  </section>

  {infographic_action}

  {source_image_evidence_block(source_original_image_src, title)}

  {coverage_audit_block(source_fields, len(asset_srcs), len(knowledge_points), case_evidence_reproduction_url, method_verification)}

  {compounding_block(insight, compounding, compounding_types, compounding_links, knowledge_points)}

  {category_section(category_judgments)}

  {related_links_block(compounding_links, taste_links, personalization_links)}

  <section class="knowledge-section">
    <h2>🧠 知識點</h2>
    {case_method_learning_block(source_fields, method_verification)}
    <div class="kp-list">
      {knowledge_point_cards(knowledge_points)}
    </div>
  </section>

  {action_questions_block(scenes)}

  {research_reuse_block(research_reuse)}

  {vault_body_block(vault_note_sections)}

  <footer>
    <p><a href="index.html">回成果頁總覽</a></p>
    <p>{escape(footer_copy)}</p>
  </footer>
</main>
<script>
(() => {{
  document.querySelectorAll("[data-infographic-command]").forEach((field) => {{
    const action = field.closest(".optional-infographic-action");
    const button = action?.querySelector("button.copy");
    const status = action?.querySelector(".status-line");
    if (!button || !status) return;
    button.addEventListener("click", async () => {{
      try {{
        if (!navigator.clipboard) throw new Error("clipboard unavailable");
        await navigator.clipboard.writeText(field.value);
        status.textContent = "已複製。貼到 AI 對話即可。";
      }} catch (error) {{
        field.classList.add("is-visible");
        field.focus();
        field.select();
        status.textContent = "已選取整段文字。請使用系統的複製功能，再貼到 AI。";
      }}
    }});
  }});
}})();
</script>
</body>
</html>
"""


def vault_body_block(sections: list[dict[str, str]]) -> str:
    if not sections:
        return ""
    details = []
    for section in sections:
        body = str(section.get("body") or "").strip()
        if not body:
            continue
        title = section.get("title") or "Obsidian 正文"
        path = section.get("path") or ""
        details.append(
            '<details class="note-details">'
            f'<summary>{escape(title)}</summary>'
            f'<p class="note-meta">{escape(path)}</p>'
            '<div class="note-shell">'
            f'{render_note_markdown(body)}'
            '</div>'
            '</details>'
        )
    if not details:
        return ""
    return (
        '<section class="vault-body">'
        '<h2>📖 已入庫正文</h2>'
        '<p class="body-intro">以下直接讀回 Obsidian 內已保存的正文；預設收合，展開後可在 HTML 內閱讀，正式真源仍是 vault 內的 Markdown 筆記。</p>'
        + "\n".join(details)
        + "</section>"
    )


def render_note_markdown(text: str) -> str:
    lines = str(text).strip().splitlines()
    if not lines:
        return '<div class="note-rendered"><p class="small">未記錄。</p></div>'

    html_parts: list[str] = []
    paragraph: list[str] = []
    index = 0

    def flush_paragraph() -> None:
        if paragraph:
            html_parts.append(f"<p>{render_inline_markdown(' '.join(paragraph))}</p>")
            paragraph.clear()

    while index < len(lines):
        line = lines[index].rstrip()
        stripped = line.strip()
        if not stripped:
            flush_paragraph()
            index += 1
            continue

        fence_match = re.match(r"^(`{3,}|~{3,})", stripped)
        if fence_match:
            flush_paragraph()
            fence = fence_match.group(1)
            fence_lines: list[str] = []
            index += 1
            while index < len(lines) and not re.fullmatch(re.escape(fence[0]) + "{" + str(len(fence)) + r",}\s*", lines[index].strip()):
                fence_lines.append(lines[index])
                index += 1
            if index < len(lines):
                index += 1
            html_parts.append(f'<pre class="note-code"><code>{escape(chr(10).join(fence_lines))}</code></pre>')
            continue

        if is_markdown_table_start(lines, index):
            flush_paragraph()
            table_lines: list[str] = []
            while index < len(lines) and is_markdown_table_line(lines[index]):
                table_lines.append(lines[index].strip())
                index += 1
            html_parts.append(render_markdown_table(table_lines))
            continue

        heading = re.match(r"^(#{1,6})\s+(.+?)\s*$", stripped)
        if heading:
            flush_paragraph()
            level = min(6, len(heading.group(1)) + 2)
            html_parts.append(f"<h{level}>{render_inline_markdown(heading.group(2))}</h{level}>")
            index += 1
            continue

        list_match = re.match(r"^([-*+])\s+(.+)$", stripped)
        ordered_match = re.match(r"^\d+[.)]\s+(.+)$", stripped)
        if list_match or ordered_match:
            flush_paragraph()
            ordered = ordered_match is not None
            tag = "ol" if ordered else "ul"
            items: list[str] = []
            pattern = r"^\d+[.)]\s+(.+)$" if ordered else r"^[-*+]\s+(.+)$"
            while index < len(lines):
                item_match = re.match(pattern, lines[index].strip())
                if not item_match:
                    break
                items.append(item_match.group(1).strip())
                index += 1
            html_parts.append(f"<{tag}>" + "".join(f"<li>{render_inline_markdown(item)}</li>" for item in items) + f"</{tag}>")
            continue

        if stripped.startswith(">"):
            flush_paragraph()
            quote_lines: list[str] = []
            while index < len(lines) and lines[index].strip().startswith(">"):
                quote_lines.append(re.sub(r"^>\s?", "", lines[index].strip()))
                index += 1
            html_parts.append(f"<blockquote>{paragraphs(chr(10).join(quote_lines))}</blockquote>")
            continue

        paragraph.append(stripped)
        index += 1

    flush_paragraph()
    return '<div class="note-rendered">' + "\n".join(html_parts) + "</div>"


def render_inline_markdown(text: str) -> str:
    rendered = escape(text)
    rendered = re.sub(r"!\[\[([^\]]+)\]\]", lambda match: f'<span class="note-ref">{match.group(1)}</span>', rendered)
    rendered = re.sub(
        r"\[\[([^\]|]+)(?:\|([^\]]+))?\]\]",
        lambda match: f'<span class="note-ref">{match.group(2) or match.group(1)}</span>',
        rendered,
    )
    rendered = _markdown_links_and_images(rendered, _linked_text_html)
    rendered = re.sub(r"`([^`]+)`", r"<code>\1</code>", rendered)
    rendered = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", rendered)
    return rendered


# Web Markdown (images and links) only changes how the reader page *shows* a
# text; stored evidence, hashes and vault notes keep the exact original string.
_MD_DEST = r"[^\s()]*(?:\([^\s()]*\)[^\s()]*)*"
_MD_TITLE = r"(?:\s+\\*(?:&quot;.*?&quot;|&#x27;.*?&#x27;|\"[^\"]*\"|'[^']*'))?"
_MD_TEXT = r"((?:\\*\[[^\[\]]*\]|\\+[\[\]]|\\+[^\[\]\\]|\[[^\[\]]*\]|[^\[\]\\])*)"
_MD_IMAGE = re.compile(r"!\[" + _MD_TEXT + r"\]\((" + _MD_DEST + r")" + _MD_TITLE + r"\)")
_MD_LINK = re.compile(r"\[" + _MD_TEXT + r"\]\((" + _MD_DEST + r")" + _MD_TITLE + r"\)")
_MD_BRACKET_ESCAPE = re.compile(r"\\+([\[\]])")


def _markdown_links_and_images(text: str, link_renderer, *, tidy: bool = False) -> str:
    """Replace web-Markdown images and links; text without them is returned unchanged.

    ``![alt](url)`` becomes its alt text (nothing when empty); ``[text](url)`` is
    handed to ``link_renderer(text, url)``.  Bare reference brackets stay text.
    """
    if "](" not in text:
        return text
    without_images = _MD_IMAGE.sub(lambda match: _MD_BRACKET_ESCAPE.sub(r"\1", match.group(1)).strip(), text)
    projected = _MD_LINK.sub(
        lambda match: link_renderer(_MD_BRACKET_ESCAPE.sub(r"\1", match.group(1)).strip(), match.group(2)),
        without_images,
    )
    if projected == text:
        return text
    return re.sub(r"[ \t]{2,}", " ", projected).strip()


def _is_web_url(url: str) -> bool:
    return re.match(r"https?://", url, flags=re.IGNORECASE) is not None


def _linked_text_html(text: str, url: str) -> str:
    """Escaped-text projection: a safe http(s) anchor, otherwise just the link text."""
    if not text:
        return ""
    if not _is_web_url(url):
        return text
    return f'<a href="{url}" target="_blank" rel="noreferrer">{text}</a>'


def _linked_text_plain(text: str, url: str) -> str:
    return text


def web_markdown_reader_text(text: str) -> str:
    """Readable plain text for a stored web-Markdown excerpt (callers escape it)."""
    return _markdown_links_and_images(str(text), _linked_text_plain, tidy=True)


def is_markdown_table_start(lines: list[str], index: int) -> bool:
    return (
        index + 1 < len(lines)
        and is_markdown_table_line(lines[index])
        and is_markdown_table_separator(lines[index + 1])
    )


def is_markdown_table_line(line: str) -> bool:
    stripped = line.strip()
    return "|" in stripped and bool(stripped.strip("|").strip())


def is_markdown_table_separator(line: str) -> bool:
    if not is_markdown_table_line(line):
        return False
    cells = markdown_table_cells(line)
    return bool(cells) and all(re.fullmatch(r":?-{3,}:?", cell.strip()) for cell in cells)


def markdown_table_cells(line: str) -> list[str]:
    cells: list[str] = []
    current: list[str] = []
    escaped = False
    for char in line.strip().strip("|"):
        if char == "|" and not escaped:
            cells.append("".join(current).strip())
            current = []
            continue
        current.append(char)
        if char == "\\":
            escaped = not escaped
        else:
            escaped = False
    cells.append("".join(current).strip())
    return [cell.replace(r"\|", "|") for cell in cells]


def render_markdown_table(lines: list[str]) -> str:
    if not lines:
        return ""
    header = markdown_table_cells(lines[0])
    body_lines = lines[2:] if len(lines) > 1 and is_markdown_table_separator(lines[1]) else lines[1:]
    header_html = "".join(f"<th>{render_inline_markdown(cell)}</th>" for cell in header)
    body_html = []
    for raw_line in body_lines:
        cells = markdown_table_cells(raw_line)
        if len(cells) < len(header):
            cells.extend([""] * (len(header) - len(cells)))
        row_html = "".join(f"<td>{render_inline_markdown(cell)}</td>" for cell in cells[: len(header)])
        body_html.append(f"<tr>{row_html}</tr>")
    return (
        '<div class="note-table-wrap"><table class="note-table">'
        f"<thead><tr>{header_html}</tr></thead>"
        f"<tbody>{''.join(body_html)}</tbody>"
        "</table></div>"
    )


def outcome_media_block(asset_srcs: list[str], supplementary_asset_srcs: list[str], title: str, asset_mode: str) -> str:
    blocks = [block for block in [infographic_gallery(asset_srcs, title, asset_mode)] if block]
    if supplementary_asset_srcs:
        blocks.append(supplementary_image_gallery(supplementary_asset_srcs, title))
    return '<div class="media-stack">' + "\n".join(blocks) + "</div>" if blocks else ""


def optional_infographic_action(source_id: str, asset_mode: str) -> str:
    """Offer the separate image workflow without turning it into a completion state."""
    if asset_mode != "knowledge_only":
        return ""
    command = f"$brain-infographic {source_id}"
    return (
        '<section class="optional-infographic-action" aria-label="可選資訊圖操作">'
        '<p>想以圖像整理這頁？</p>'
        f'<textarea class="copy-source" data-infographic-command readonly aria-label="為這頁製作資訊圖指令">{escape(command)}</textarea>'
        '<button class="copy" type="button">為這頁製作資訊圖</button>'
        '<span class="status-line" role="status" aria-live="polite"></span>'
        '</section>'
    )


def escape(value: object) -> str:
    return html.escape(str(value), quote=True)


def strip_trailing_whitespace(text: str) -> str:
    return "\n".join(line.rstrip() for line in text.splitlines()) + "\n"


def infographic_gallery(asset_srcs: list[str], title: str, asset_mode: str = "formal_infographic") -> str:
    if asset_mode == "knowledge_only":
        return ""
    figures = []
    total = len(asset_srcs)
    for index, asset_src in enumerate(asset_srcs, start=1):
        if asset_mode == "case_evidence":
            label = case_evidence_caption(asset_src, index, total)
        else:
            label = f"資訊圖 {index}" if total > 1 else "資訊圖"
        figures.append(
            '<figure class="image-wrap">'
            f'<img src="{escape(asset_src)}" alt="{escape(title)} {escape(label)}">'
            f'<figcaption>{escape(label)}</figcaption>'
            "</figure>"
        )
    return '<div class="image-gallery">' + "\n".join(figures) + "</div>"


def case_evidence_caption(asset_src: str, index: int, total: int) -> str:
    """Keep original-site evidence visibly distinct from the local equivalent."""
    path = asset_src.replace("\\", "/").lower()
    labels = (
        ("screenshot-source-desktop-initial", "原站桌面截圖"),
        ("screenshot-source-desktop-mid-scroll", "原站捲動截圖"),
        ("screenshot-source-mobile-initial", "原站手機截圖"),
        ("reproduction-comparison-local", "本地等效重現截圖"),
    )
    for marker, label in labels:
        if marker in path:
            return label
    return f"案例截圖 {index}" if total > 1 else "案例截圖"


def reader_title_from_saved_source(source_fields: dict[str, object], source_body: str) -> str:
    """Use the retained source-audit title only to repair a legacy escaped-table split."""
    title = str(source_fields.get("title") or "").strip()
    if not title.endswith("\\"):
        return title
    base = title.rstrip("\\").rstrip()
    for line in source_body.splitlines():
        cells = markdown_table_cells(line)
        if (
            len(cells) >= 2
            and cells[0] == "來源名稱"
            and cells[1]
            and cells[1] != title
            and cells[1].startswith(base)
        ):
            return cells[1]
    # Do not alter a literal trailing backslash without a retained-table value
    # proving it was the leftover escape from a split `\|` table cell.
    return title


def case_evidence_reproduction_entrypoint_path(
    vault: Path,
    source_id: str,
    source_fields: dict[str, object],
) -> Path | None:
    if str(source_fields.get("asset_mode") or "") != "case_evidence":
        return None
    asset_rels = list_value(source_fields.get("case_evidence_assets"))
    entrypoints = [
        asset_rel
        for asset_rel in asset_rels
        if "reproduction-entrypoint-" in Path(asset_rel).name and Path(asset_rel).suffix.lower() == ".html"
    ]
    if len(entrypoints) != 1:
        raise ValueError(
            f"{source_id} case evidence reproduction entrypoint is missing or ambiguous in case_evidence_assets"
        )
    entrypoint = (vault / entrypoints[0]).resolve()
    if entrypoint != vault.resolve() and vault.resolve() not in entrypoint.parents:
        raise ValueError(f"{source_id} case evidence reproduction entrypoint is outside the vault")
    if not entrypoint.is_file():
        raise ValueError(f"{source_id} case evidence reproduction entrypoint is missing: {entrypoints[0]}")
    return entrypoint


def research_method_verification(vault: Path, source_id: str) -> dict[str, str]:
    """Read the formal research state only to label a derived reader projection.

    A method demotion must be visible to the reader without rewriting the
    receipt-bound source, application or historical evidence files.
    """
    registry = vault / "_registry" / "research-programs"
    if not registry.is_dir():
        return {}
    matches: list[dict[str, str]] = []
    for state_path in sorted(registry.glob("*/state.json")):
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        cases = state.get("accepted_cases") if isinstance(state, dict) else None
        if not isinstance(cases, list):
            continue
        for case in cases:
            if not isinstance(case, dict) or str(case.get("source_id") or "").strip() != source_id:
                continue
            verification = case.get("method_verification")
            if not isinstance(verification, dict):
                continue
            status = str(verification.get("state") or "").strip()
            if not status:
                continue
            matches.append({
                "state": status,
                "reason": str(verification.get("reason") or "").strip(),
                "topic_id": str(state.get("topic_id") or state_path.parent.name).strip(),
            })
    if not matches:
        return {}
    if len(matches) != 1:
        return {"state": "needs_revalidation", "reason": "同一來源的研究方法狀態不一致，需先重驗。"}
    return matches[0]


def supplementary_image_gallery(asset_srcs: list[str], title: str) -> str:
    figures = []
    total = len(asset_srcs)
    for index, asset_src in enumerate(asset_srcs, start=1):
        label = f"補充圖 {index}" if total > 1 else "補充圖"
        figures.append(
            '<figure class="image-wrap">'
            f'<img src="{escape(asset_src)}" alt="{escape(title)} {escape(label)}">'
            f'<figcaption>{escape(label)}：保留版本，不計入正式資訊圖覆蓋。</figcaption>'
            "</figure>"
        )
    return '<div class="image-gallery supplementary-gallery">' + "\n".join(figures) + "</div>"


def source_image_evidence_block(source_original_image_src: str, title: str) -> str:
    if not source_original_image_src:
        return ""
    return (
        '<section class="source-evidence">'
        '<div class="panel">'
        '<h2>🖼️ 原始圖片證據</h2>'
        '<p class="small">這是原圖；視覺解讀、知識點和主張均應回指這份證據，而非把資訊圖當成來源。</p>'
        f'<a href="{escape(source_original_image_src)}" target="_blank" rel="noreferrer">開啟原圖</a>'
        f'<img src="{escape(source_original_image_src)}" alt="{escape(title)} 原始圖片證據">'
        '</div></section>'
    )


def case_method_learning_block(source_fields: dict[str, object], method_verification: dict[str, str] | None = None) -> str:
    body = str(source_fields.get("_case_method_body") or "").strip()
    if str(source_fields.get("asset_mode") or "") != "case_evidence" or not body:
        return ""
    pending = (method_verification or {}).get("state") == "needs_revalidation"
    heading = "案例方法（待重驗）" if pending else "案例觀察與實作方法"
    rendered = render_note_markdown(re.sub(r"(?m)^#### ", "## ", body))
    return (
        '<div class="panel case-method-learning">'
        f'<h3>{heading}</h3>'
        '<p class="small">以下來自案例觀察與本地實作；與下方文章原文知識點分開閱讀。</p>'
        f'{rendered}</div>'
        '<h3>文章原文知識點</h3>'
    )


def coverage_audit_block(
    source_fields: dict[str, object],
    asset_count: int,
    kp_count: int,
    case_evidence_reproduction_url: str = "",
    method_verification: dict[str, str] | None = None,
) -> str:
    asset_mode = str(source_fields.get("asset_mode") or "").strip()
    if asset_mode == "knowledge_only":
        return ""
    if asset_mode == "case_evidence":
        metrics = [
            ("資產模式", "case_evidence"),
            ("已讀截圖", str(asset_count)),
            ("知識點", str(kp_count)),
        ]
        metric_html = "".join(
            f'<div class="metric"><span>{escape(label)}</span><b>{escape(value)}</b></div>'
            for label, value in metrics
        )
        verification = method_verification or {}
        needs_revalidation = verification.get("state") == "needs_revalidation"
        warning = (
            '<div class="panel action">'
            '<h3>⚠️ 方法驗證待重驗</h3>'
            '<p>來源、原始收據和案例位置仍保留；現有本地頁面沒有證明所選的核心方法，不能當作可重用方法或成功範本。</p>'
            + (f'<p class="small">原因：{escape(verification.get("reason") or "未記錄")}</p>' if verification.get("reason") else "")
            + '</div>'
        ) if needs_revalidation else ""
        link_label = "查看歷史本地試驗（未通過方法驗證）" if needs_revalidation else "預覽應用／試驗（本地最小重現）"
        description = (
            "<p>此頁展示原站截圖及歷史本地試驗；不是額外生成的資訊圖。</p>"
            if needs_revalidation
            else "<p>此頁展示原站截圖與本地最小重現對照；不是額外生成的資訊圖。</p>"
        )
        return "".join((
            "<section id=\"research-applications\"><div class=\"panel\"><h2>🧪 案例證據</h2>",
            description,
            f'<div class="metric-row">{metric_html}</div>',
            '<p class="research-work-actions">',
            f'<a class="research-work-link" href="{escape(case_evidence_reproduction_url)}" target="_blank" rel="noreferrer">{link_label}</a>',
            f"</p>{warning}</div></section>",
        ))
    status = str(source_fields.get("coverage_audit_status") or "").strip()
    if not status or status == "missing":
        return ""
    total = intish(source_fields.get("coverage_unit_total"))
    mapped = intish(source_fields.get("coverage_unit_infographic_mapped"))
    omitted = intish(source_fields.get("coverage_unit_omitted"))
    summary_only = intish(source_fields.get("coverage_unit_summary_only"))
    page_count = intish(source_fields.get("infographic_page_count")) or asset_count
    multi_volume = str(source_fields.get("infographic_multi_volume_status") or "").strip()
    metrics = [
        ("覆蓋狀態", display_coverage_status(status)),
        ("重要單元", str(total)),
        ("入圖單元", str(mapped)),
        ("資訊圖", str(page_count)),
        ("知識點", str(kp_count)),
        ("明確省略", str(omitted)),
        ("只作摘要", str(summary_only)),
        ("分卷狀態", display_multi_volume_status(multi_volume)),
    ]
    metric_html = "".join(
        f'<div class="metric"><span>{escape(label)}</span><b>{escape(value)}</b></div>'
        for label, value in metrics
    )
    summary = (
        f"本來源以 {total} 個重要單元作覆蓋基準，"
        f"{mapped} 個已映射到資訊圖，正式成果頁讀回 {asset_count} 張 PNG。"
    )
    if omitted:
        summary += f" 有 {omitted} 項明確省略，省略理由保留在 Obsidian source note。"
    return (
        "<section>"
        '<div class="panel">'
        "<h2>📌 本來源覆蓋狀態</h2>"
        f"<p>{escape(summary)}</p>"
        f'<div class="metric-row">{metric_html}</div>'
        "</div>"
        "</section>"
    )


def intish(value: object) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return 0


def display_coverage_status(value: str) -> str:
    return {
        "complete": "完整",
        "needs_review": "需覆核",
        "blocked": "受阻",
    }.get(value, value)


def display_multi_volume_status(value: str) -> str:
    return {
        "single_page": "單頁",
        "standard_multi_page": "多頁",
        "multi_volume_source": "多卷來源",
        "batch_limit_exceeded": "超出單批上限",
    }.get(value, value or "未記錄")


def breakable_text(value: object) -> str:
    escaped = escape(value)
    return re.sub(r"([/()：:—–-])", r"\1<wbr>", escaped)


def display_page_title(title: str) -> str:
    normalized = re.sub(r"\s+", " ", title).strip()
    return re.sub(r"\s+([：:])", r"\1", normalized)


def display_status(value: str) -> str:
    return {
        "has_insight": "有洞見",
        "needs_context": "需更多脈絡",
        "no_compounding_insight": "暫無複利洞見",
        "linked": "已扣連",
        "not_linked": "未扣連",
        "blocked": "受阻",
    }.get(value, value)


def compact_text(text: str, *, limit: int) -> str:
    normalized = re.sub(r"\s+", " ", text).strip()
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 1].rstrip() + "…"


def relationship_block(relationship: str, app_fields: dict[str, object], source_fields: dict[str, object]) -> str:
    criteria = relationship_criteria(app_fields)
    updated = str(app_fields.get("updated") or source_fields.get("updated") or source_fields.get("capture_date") or "")
    update_note = (
        f"這是截至 {updated} 的判斷；日後有更多同類材料或新的採納結果時，這段關係應重新扣連和更新。"
        if updated
        else "這是目前資料脈絡下的判斷；日後有更多同類材料或新的採納結果時，這段關係應重新扣連和更新。"
    )
    return (
        paragraphs(relationship)
        + "\n"
        + '<div class="relationship-meta">'
        + "<h3>判斷基準</h3>"
        + unordered(criteria)
        + "<h3>判斷更新時間</h3>"
        + f"<p>{escape(update_note)}</p>"
        + "</div>"
    )


def relationship_criteria(app_fields: dict[str, object]) -> list[str]:
    criteria: list[str] = []
    relevance = str(app_fields.get("personal_relevance") or "").strip()
    if relevance:
        criteria.append(f"個人相關度：{relevance}")
    adoption = str(app_fields.get("ai_adoption_reason") or app_fields.get("adoption_reason") or "").strip()
    if adoption:
        criteria.append(f"採納理由：{adoption}")
    scenes = list_value(app_fields.get("life_scenes"))
    if scenes:
        criteria.append("相關場景：" + "、".join(scenes))
    action_status = str(app_fields.get("action_status") or "").strip()
    if action_status:
        criteria.append(f"行動狀態：{action_status}")
    taste_reason = str(app_fields.get("taste_network_reason") or "").strip()
    if taste_reason:
        criteria.append(f"品味判斷：{taste_reason}")
    return criteria


def paragraphs(text: str) -> str:
    parts = [part.strip() for part in text.split("\n\n") if part.strip()]
    if not parts:
        return '<p class="small">未記錄。</p>'
    return "\n".join(f"<p>{escape(part)}</p>" for part in parts)


def unordered(items: list[str], empty_text: str = "未記錄。") -> str:
    if not items:
        return f'<p class="small">{escape(empty_text)}</p>'
    return "<ul>" + "".join(f"<li>{escape(item)}</li>" for item in items) + "</ul>"


def linked_unordered(items: list[str], empty_text: str = "未記錄。") -> str:
    if not items:
        return f'<p class="small">{escape(empty_text)}</p>'
    return "<ul>" + "".join(f"<li>{source_id_links(item)}</li>" for item in items) + "</ul>"


def compounding_block(
    insight: str,
    compounding: str,
    compounding_types: list[str],
    compounding_links: list[str],
    knowledge_points: list[dict[str, object]],
) -> str:
    if not meaningful_text(insight) and not compounding_types and not compounding_links:
        return ""
    metrics = [
        ("洞見狀態", compounding),
        ("知識點", str(len(knowledge_points))),
    ]
    if compounding_types:
        metrics.insert(1, ("洞見類型", " / ".join(compounding_types)))
    if compounding_links:
        metrics.insert(-1, ("扣連數", str(len(compounding_links))))
    metric_html = "".join(
        f'<div class="metric"><span>{escape(label)}</span><b>{escape(value)}</b></div>'
        for label, value in metrics
    )
    return (
        "<section>"
        '<div class="panel">'
        "<h2>⚡ 複利洞見</h2>"
        f"{paragraphs(insight) if meaningful_text(insight) else ''}"
        f'<div class="metric-row">{metric_html}</div>'
        "</div>"
        "</section>"
    )


def meaningful_text(text: str) -> bool:
    normalized = re.sub(r"\s+", " ", text).strip()
    return bool(normalized and normalized not in {"未記錄。", "未記錄"})


def related_links_block(
    compounding_links: list[str],
    taste_links: list[str],
    personalization_links: list[str],
) -> str:
    groups = related_link_groups(compounding_links, taste_links, personalization_links)
    if not groups:
        return ""
    return (
        "<section>"
        "<h2>🔗 相關扣連</h2>"
        '<div class="grid three">'
        f"{groups}"
        "</div>"
        "</section>"
    )


def related_link_groups(
    compounding_links: list[str],
    taste_links: list[str],
    personalization_links: list[str],
) -> str:
    grouped_items = dedupe_related_items(
        [
            ("複利扣連", compounding_links, "尚未形成複利扣連。"),
            ("品味脈絡", taste_links, "尚未形成明確品味脈絡。"),
            ("個人脈絡", personalization_links, "尚未形成明確個人脈絡。"),
        ]
    )
    return "\n".join(
        f'<div class="panel link-group"><h3>{escape(title)}</h3>{linked_unordered(items, empty_text=empty_text)}</div>'
        for title, items, empty_text in grouped_items
        if items
    )


def dedupe_related_items(groups: list[tuple[str, list[str], str]]) -> list[tuple[str, list[str], str]]:
    seen: set[str] = set()
    deduped_groups: list[tuple[str, list[str], str]] = []
    for title, items, empty_text in groups:
        deduped_items: list[str] = []
        for item in items:
            key = related_item_key(item)
            if key in seen:
                continue
            seen.add(key)
            deduped_items.append(item)
        deduped_groups.append((title, deduped_items, empty_text))
    return deduped_groups


def related_item_key(item: str) -> str:
    match = re.search(r"\b(KS-VS-\d{4})\b", item)
    if match:
        return match.group(1)
    return re.sub(r"\s+", " ", item).strip()


def source_id_links(text: str) -> str:
    escaped = escape(text)
    return re.sub(
        r"\b(KS-VS-\d{4})\b",
        lambda match: f'<a href="{match.group(1)}_outcome.html">{match.group(1)}</a>',
        escaped,
    )


def category_cards(categories: dict[str, str]) -> str:
    categories = {
        label: text
        for label, text in categories.items()
        if meaningful_category_text(text)
    }
    values = [text for text in categories.values() if text]
    if values and len(set(values)) == 1 and len(categories) > 1:
        labels = "、".join(categories.keys())
        return (
            '<div class="panel category">'
            "<h3>整體判斷</h3>"
            f"<p>{escape(labels)}：{escape(values[0])}</p>"
            "</div>"
        )
    return "\n".join(
        f'<div class="panel category"><h3>{escape(label)}</h3><p>{escape(text)}</p></div>'
        for label, text in categories.items()
    )


def category_section(categories: dict[str, str]) -> str:
    cards = category_cards(categories)
    if not cards:
        return ""
    return (
        "<section>"
        "<h2>🧩 五類複利判斷</h2>"
        '<div class="grid five">'
        f"{cards}"
        "</div>"
        "</section>"
    )


def meaningful_category_text(text: str) -> bool:
    if not meaningful_text(text):
        return False
    internal_status_fragments = (
        "舊資料只完成機制欄位補齊",
        "未作重新判斷",
    )
    return not any(fragment in text for fragment in internal_status_fragments)


def knowledge_point_cards(points: list[dict[str, object]]) -> str:
    if not points:
        return '<p class="small">未記錄。</p>'
    cards = []
    seen_anchors: set[str] = set()
    for point in points:
        title = point.get("knowledge_point") or point.get("definition") or point.get("kp_id") or "未命名知識點"
        kind = point.get("knowledge_type") or ""
        confidence = point.get("confidence") or ""
        anchor = point.get("source_anchor") or ""
        normalized_anchor = normalized_visible_text(str(anchor))
        if normalized_anchor == normalized_visible_text(str(title)) or normalized_anchor in seen_anchors:
            anchor_text = ""
        else:
            anchor_text = web_markdown_reader_text(str(anchor))
            if normalized_anchor:
                seen_anchors.add(normalized_anchor)
        cards.append(
            '<div class="panel kp">'
            f"<h3>{escape(title)}</h3>"
            f'<p class="small kp-meta"><span>{escape(kind)}</span><span>信心 {escape(confidence)}</span></p>'
            + (
                '<details class="kp-evidence">'
                '<summary>查看原文依據</summary>'
                f'<p class="kp-anchor">{escape(anchor_text)}</p>'
                '</details>'
                if anchor_text else ""
            )
            + "</div>"
        )
    return "\n".join(cards)


def action_questions_block(scenes: list[str]) -> str:
    if not scenes:
        return ""
    return (
        '<section class="panel action">'
        "<h2>✅ 可行動驗證問題</h2>"
        f"{unordered(scenes)}"
        "</section>"
    )


def research_reuse_block(applications: list[dict[str, object]]) -> str:
    """Keep a source's research-derived work visible without relabelling it as a source."""
    if not applications:
        return ""
    cards: list[str] = []
    for application in applications:
        evidence = application.get("evidence") if isinstance(application.get("evidence"), dict) else {}
        reuse_context = application.get("reuse_context") if isinstance(application.get("reuse_context"), dict) else {}
        lines = [
            ("狀態", research_reader_text(application.get("status")) or "未知"),
            ("可重用做法", research_reader_text(reuse_context.get("method"))),
            *research_assessment_lines(reuse_context, evidence),
            ("限制", research_reader_text(application.get("limitations"))),
            ("經驗", research_reader_text(application.get("lessons"))),
            ("相關應用", research_reader_text(reuse_context.get("related_applications"))),
        ]
        body = "".join(
            f"<p><strong>{escape(label)}：</strong>{escape(text)}</p>"
            for label, text in lines if text
        )
        work_links = research_reuse_link_rows(application.get("work_links"))
        if work_links:
            application_title = research_reader_text(application.get("title")) or "研究應用作品"
            names = "、".join(
                escape(research_work_label(application_title, str(row["name"]), len(work_links)))
                for row in work_links
            )
            body = (
                f'<p><strong>本機作品：</strong>{names}</p>'
                + '<p class="research-work-actions">'
                + " ".join(
                    f'<a class="research-work-link" href="{escape(str(row["url"]))}">'
                    f'打開本機作品：{escape(research_work_label(application_title, str(row["name"]), len(work_links)))}</a>'
                    for row in work_links
                )
                + "</p>"
                + body
            )
        evidence_links = research_reuse_link_rows(application.get("evidence_links"))
        if evidence_links:
            body += '<p><strong>本機截圖／證據：</strong>' + " ".join(
                f'<a href="{escape(str(row["url"]))}">{escape(str(row["name"]))}</a>'
                for row in evidence_links
            ) + "</p>"
        links = application.get("links") if isinstance(application.get("links"), list) else []
        safe_links = [row for row in links if isinstance(row, dict) and row.get("label") and row.get("url")]
        if safe_links:
            body += '<p><strong>回查筆記：</strong>' + " ".join(
                f'<a href="{escape(str(row["url"]))}">{escape(str(row["label"]))}</a>'
                for row in safe_links
            ) + "</p>"
        cards.append(
            '<article class="panel link-group">'
            f'<h3>{escape(research_reader_text(application.get("title")) or "未命名研究應用")}</h3>'
            + body
            + "</article>"
        )
    if not cards:
        return ""
    return (
        '<section id="research-applications" class="research-reuse">'
        '<h2>🧪 研究應用／作品／試驗</h2>'
        '<p class="body-intro">這些是可再找回的衍生應用，不增加正式來源數；各項評估維持原筆記的證據狀態。</p>'
        '<div class="grid">'
        + "".join(cards)
        + "</div></section>"
    )


def research_reuse_navigation(applications: list[dict[str, object]]) -> str:
    count = sum(1 for application in applications if research_reuse_link_rows(application.get("work_links")))
    return f'<a href="#research-applications">預覽應用作品（{count}）</a>' if count else ""


def research_reuse_link_rows(value: object) -> list[dict[str, object]]:
    return [
        row for row in value if isinstance(row, dict) and row.get("name") and row.get("url")
    ] if isinstance(value, list) else []


def research_work_label(application_title: str, filename: str, work_count: int) -> str:
    return application_title if work_count == 1 else f"{application_title}：{filename}"


def research_reader_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, tuple, set)):
        return "、".join(part for part in (research_reader_text(item) for item in value) if part)
    if isinstance(value, dict):
        return "；".join(
            f"{key}：{text}" for key, item in value.items()
            if (text := research_reader_text(item))
        )
    return str(value).strip()


def research_assessment_lines(context: dict[str, object], evidence: dict[str, object]) -> list[tuple[str, str]]:
    """Display every saved assessment dimension; legacy evidence remains readable."""
    saved = context.get("assessments")
    if isinstance(saved, list):
        lines: list[tuple[str, str]] = []
        for assessment in saved:
            if not isinstance(assessment, dict):
                continue
            dimension = research_reader_text(assessment.get("dimension"))
            text = research_assessment_text(assessment.get("status"), research_reader_text(assessment.get("summary")))
            if dimension and text:
                lines.append((research_assessment_label(dimension), text))
        return lines
    return [
        (research_assessment_label(research_reader_text(dimension)), research_reader_text(value))
        for dimension, value in evidence.items()
        if research_reader_text(dimension) and research_reader_text(value)
    ]


def research_assessment_label(dimension: str) -> str:
    return {
        "technical_effectiveness": "技術有效性",
        "aesthetic_quality": "美學品質",
        "context_fit": "情境適配",
    }.get(dimension, dimension.replace("_", " "))


def normalized_visible_text(value: str) -> str:
    value = re.sub(r"^#+\s*", "", value.strip())
    return re.sub(r"\s+", " ", value).strip()


def write_paginated_index(entries: list[dict[str, object]], output_dir: Path, page_size: int,
                          vault: Path | None = None, defer_nebula: bool = False,
                          nebula_report: dict[str, object] | None = None) -> list[Path]:
    """Keep the caller API while routing all catalogue UI through one renderer."""
    try:
        from tools.outcome_catalog import write_catalog_pages
    except ModuleNotFoundError:
        from outcome_catalog import write_catalog_pages
    answers = []
    research = research_catalog_projection(vault) if vault is not None else {"topics": [], "materials": [], "snapshot": ""}
    catalog_entries = enrich_research_catalog_entries(entries, research["materials"], vault=vault)
    if vault is not None and not defer_nebula:
        try:
            from .knowledge_nebula import refresh_installed
        except ImportError:
            from knowledge_nebula import refresh_installed
        refreshed = refresh_installed(vault, output_dir)
        if nebula_report is not None:  # None: no nebula installed here; a pause is reported by the callers, never hidden
            nebula_report["nebula"] = refreshed
    if vault is not None and output_dir.resolve().is_relative_to(vault.resolve()):
        try:
            from . import thinking_archive as archive
        except ImportError:
            import thinking_archive as archive
        with archive.lock(vault, output_dir / '.thinking-catalog.lock'):
            for name in ('index.html', 'knowledge.html', 'thinking.html'):
                archive.safe(vault, output_dir / name)
            answers = archive.export_records(vault, output_dir)
            paths = write_catalog_pages(
                catalog_entries, output_dir, page_size, answers, vault=vault,
                research_topics=research["topics"], research_materials=research["materials"], research_snapshot=research["snapshot"],
            )
    else:
        paths = write_catalog_pages(catalog_entries, output_dir, page_size)
    # Existing bookmarks remain readable; only owned legacy page-NNN outputs
    # change. No source outcome page is regenerated or removed here.
    try:
        from .reader_shell import atomic_reader_write, catalogue_projection_lock
    except ImportError:
        from reader_shell import atomic_reader_write, catalogue_projection_lock
    with catalogue_projection_lock(output_dir):
        for legacy in output_dir.glob("page-[0-9][0-9][0-9].html"):
            if legacy.resolve().parent != output_dir.resolve():
                raise ValueError("legacy catalogue output escapes output directory")
            number = int(legacy.stem.split("-")[1])
            target = f"knowledge.html?page={number}"
            atomic_reader_write(
                legacy,
                '<!doctype html><html lang="zh-Hant"><meta charset="utf-8">'
                '<meta name="viewport" content="width=device-width,initial-scale=1">'
                '<title>成果目錄已更新</title><h1>成果目錄已更新</h1>'
                f'<p><a href="{target}">繼續閱讀知識成果第 {number} 頁</a></p>'
                '<p>知識成果與思考紀錄可由成果總覽找回。</p></html>',
            )
    return paths


def research_catalog_projection(vault: Path) -> dict[str, object]:
    """Read the shared research projection without making it a second reader owner."""
    empty: dict[str, object] = {"topics": [], "materials": [], "snapshot": ""}
    try:
        from tools.research_catalog import list_research_topics, search_research_materials
    except ModuleNotFoundError:
        try:
            from research_catalog import list_research_topics, search_research_materials
        except ModuleNotFoundError:
            return empty
    try:
        root = vault.resolve().parent
        topic_result = list_research_topics(root)
        mapping_result = search_research_materials(root, query="", include_applications=True)
    except (OSError, ValueError, TypeError):
        return empty
    topics = topic_result.get("topics") if isinstance(topic_result, dict) else []
    materials = mapping_result.get("materials") if isinstance(mapping_result, dict) else []
    snapshot = ""
    if isinstance(mapping_result, dict):
        snapshot = str(mapping_result.get("checked_at") or "")
    if not snapshot and isinstance(topic_result, dict):
        snapshot = str(topic_result.get("generated_at") or "")
    return {
        "topics": topics if isinstance(topics, list) else [],
        "materials": materials if isinstance(materials, list) else [],
        "snapshot": snapshot,
    }


def research_reuse_work_paths_for_source(vault: Path, source_id: str) -> list[str]:
    projection = research_catalog_projection(vault)
    paths: list[str] = []
    for material in projection["materials"]:
        if not isinstance(material, dict):
            continue
        if str(material.get("source_id") or "").strip() != source_id or material.get("material_kind") != "application":
            continue
        context = material.get("reuse_context") if isinstance(material.get("reuse_context"), dict) else {}
        for path in research_reuse_paths(material, context, "work_paths", "work_path", "work_files"):
            if path not in paths:
                paths.append(path)
    return paths


def research_reuse_work_path_issues(vault: Path, source_id: str) -> list[str]:
    return [
        f"{source_id} research reuse work path is missing or unsafe: {path}"
        for path in research_reuse_work_paths_for_source(vault, source_id)
        if research_reader_file(vault, path) is None
    ]


def has_case_evidence_reproduction_entrypoint(vault: Path, source_id: str) -> bool:
    """Count a case reproduction only when its saved source declares one readable entrypoint."""
    try:
        source_note = resolve_source_note(vault, source_id)
        source_fields, _ = read_markdown_record(source_note)
        return case_evidence_reproduction_entrypoint_path(vault, source_id, source_fields) is not None
    except (OSError, ValueError, SystemExit):
        return False


def research_reuse_for_source(vault: Path, source_id: str, output_dir: Path) -> list[dict[str, object]]:
    """Project linked applications through the shared research reader API only."""
    projection = research_catalog_projection(vault)
    materials = [
        dict(row) for row in projection["materials"] if isinstance(row, dict)
        and str(row.get("source_id") or "").strip() == source_id
        and row.get("material_kind") == "application"
    ]
    if not materials:
        return []
    try:
        from tools.research_catalog import inspect_research_topic
    except ModuleNotFoundError:
        try:
            from research_catalog import inspect_research_topic
        except ModuleNotFoundError:
            inspect_research_topic = None
    details_by_id: dict[str, dict[str, object]] = {}
    if inspect_research_topic is not None:
        root = vault.resolve().parent
        for topic_id in sorted({str(item).strip() for row in materials for item in row.get("topic_ids", []) if str(item).strip()}):
            try:
                inspected = inspect_research_topic(root, topic_id)
            except (OSError, ValueError, TypeError):
                continue
            applications = inspected.get("applications") if isinstance(inspected, dict) else []
            for application in applications if isinstance(applications, list) else []:
                if not isinstance(application, dict) or str(application.get("source_id") or "").strip() != source_id:
                    continue
                application_id = str(application.get("application_id") or "").strip()
                if application_id:
                    details_by_id[application_id] = application
    projected: list[dict[str, object]] = []
    seen: set[str] = set()
    for material in materials:
        application_id = str(material.get("application_id") or "").strip()
        details = details_by_id.get(application_id, {})
        merged = dict(material)
        merged.update({key: value for key, value in details.items() if value is not None})
        identity = str(merged.get("application_id") or merged.get("application_note_path") or merged.get("title") or "").strip()
        if not identity or identity in seen:
            continue
        seen.add(identity)
        links = []
        for label, key in (("原文筆記", "source_note_path"), ("應用筆記", "application_note_path")):
            url = research_reader_local_url(vault, output_dir, merged.get(key))
            if url:
                links.append({"label": label, "url": url})
        reuse_context = merged.get("reuse_context") if isinstance(merged.get("reuse_context"), dict) else {}
        work_links: list[dict[str, object]] = []
        seen_local_paths: set[str] = set()
        for value in research_reuse_paths(merged, reuse_context, "work_paths", "work_path", "work_files"):
            url = research_reader_local_url(vault, output_dir, value)
            if url:
                normalized = str(value).replace("\\", "/").strip()
                seen_local_paths.add(normalized)
                work_links.append({"name": Path(value).name, "url": url})
        evidence_links: list[dict[str, object]] = []
        for value in research_reuse_paths(merged, reuse_context, "evidence_paths", "evidence_path", "citations"):
            normalized = str(value).replace("\\", "/").strip()
            if normalized in seen_local_paths:
                continue
            url = research_reader_local_url(vault, output_dir, value)
            if url:
                evidence_links.append({"name": Path(value).name, "url": url})
        evidence = merged.get("evidence") if isinstance(merged.get("evidence"), dict) else {}
        if not evidence:
            assessment_summaries = {
                str(row.get("dimension") or ""): str(row.get("summary") or "").strip()
                for row in reuse_context.get("assessments", [])
                if isinstance(row, dict) and str(row.get("dimension") or "").strip()
            }
            evidence = {
                key: research_assessment_text(reuse_context.get(key), assessment_summaries.get(key, ""))
                for key in ("technical_effectiveness", "aesthetic_quality", "context_fit")
                if reuse_context.get(key) is not None
            }
        projected.append({
            "title": merged.get("title") or application_id or "未命名研究應用",
            "status": merged.get("status") or "unknown",
            "reuse_context": reuse_context,
            "evidence": evidence,
            "limitations": merged.get("limitations") or reuse_context.get("limitations"),
            "lessons": merged.get("lessons") or reuse_context.get("lesson"),
            "links": links,
            "work_links": work_links,
            "evidence_links": evidence_links,
        })
    return projected


def research_assessment_text(value: object, summary: str) -> str:
    raw = str(value or "").strip()
    status = {
        "verified": "已核實",
        "insufficient": "不足，不能作優秀範本",
        "unverified": "未核實",
    }.get(raw.casefold(), raw)
    return f"{status} — {summary}" if status and summary else status or summary


def research_reuse_paths(record: dict[str, object], context: dict[str, object], *keys: str) -> list[str]:
    paths: list[str] = []
    for key in keys:
        for value in (record.get(key), context.get(key)):
            values = value if isinstance(value, list) else [value]
            for item in values:
                text = str(item or "").strip()
                if text and text not in paths:
                    paths.append(text)
    return paths


def research_reader_local_url(vault: Path, output_dir: Path, value: object) -> str:
    target = research_reader_file(vault, value)
    if target is None:
        return ""
    try:
        return relative_url(target, output_dir.resolve())
    except (OSError, ValueError):
        return ""


def research_reader_file(vault: Path, value: object) -> Path | None:
    """Return only a read-back vault file; a caller can never link outside the vault."""
    text = str(value or "").replace("\\", "/").strip()
    if not text or text.startswith(("/", "//")) or ".." in text.split("/") or re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", text):
        return None
    try:
        target = (vault.resolve() / Path(text)).resolve()
        target.relative_to(vault.resolve())
        if not target.is_file():
            return None
        return target
    except (OSError, ValueError):
        return None


def enrich_research_catalog_entries(
    entries: list[dict[str, object]], materials: object, *, vault: Path | None = None,
) -> list[dict[str, object]]:
    mappings: dict[str, dict[str, object]] = {}
    for material in materials if isinstance(materials, list) else []:
        if not isinstance(material, dict):
            continue
        source_id = str(material.get("source_id") or "").strip()
        if not source_id:
            continue
        mapping = mappings.setdefault(source_id, {"topic_ids": set(), "facets": {}, "readable_applications": set()})
        topic_ids = material.get("topic_ids") if isinstance(material.get("topic_ids"), list) else []
        mapping["topic_ids"].update(str(item).strip() for item in topic_ids if str(item).strip())
        classifications = material.get("classifications") if isinstance(material.get("classifications"), dict) else {}
        facets = mapping["facets"]
        for name, values in classifications.items():
            if not isinstance(values, list):
                continue
            facets.setdefault(str(name), set()).update(str(item).strip() for item in values if str(item).strip())
        nested_values = classifications.get("values") if isinstance(classifications.get("values"), dict) else {}
        for name, values in nested_values.items():
            if not isinstance(values, list):
                continue
            facet_name = {
                "design_family": "design_families",
                "coverage_dimension": "coverage_dimensions",
                "application_scene": "application_scenes",
            }.get(str(name), str(name))
            facets.setdefault(facet_name, set()).update(str(item).strip() for item in values if str(item).strip())
        if vault is not None and material.get("material_kind") == "application":
            context = material.get("reuse_context") if isinstance(material.get("reuse_context"), dict) else {}
            has_work = any(
                research_reader_file(vault, path) is not None
                for path in research_reuse_paths(material, context, "work_paths", "work_path", "work_files")
            )
            if has_work:
                application_id = str(
                    material.get("application_id") or material.get("application_note_path") or material.get("title") or ""
                ).strip()
                if application_id:
                    mapping["readable_applications"].add(application_id)
    enriched: list[dict[str, object]] = []
    for entry in entries:
        row = dict(entry)
        source_id = str(row.get("source_id") or "")
        mapping = mappings.get(source_id, {})
        row["research_topic_ids"] = sorted(mapping.get("topic_ids", set()))
        row["research_facets"] = {name: sorted(values) for name, values in mapping.get("facets", {}).items() if values}
        readable_count = len(mapping.get("readable_applications", set()))
        case_reproduction_count = int(vault is not None and has_case_evidence_reproduction_entrypoint(vault, source_id))
        row["research_application_count"] = readable_count + case_reproduction_count
        enriched.append(row)
    return enriched


def cleanup_retired_taste_routes(output_dir: Path) -> list[Path]:
    cleaned: list[Path] = []
    try:
        from .reader_shell import atomic_reader_write, catalogue_projection_lock
    except ImportError:
        from reader_shell import atomic_reader_write, catalogue_projection_lock
    with catalogue_projection_lock(output_dir):
        for page_path in sorted(output_dir.glob("*_outcome.html")):
            original = page_path.read_text(encoding="utf-8")
            updated = remove_retired_taste_routes_from_html(original)
            if updated != original:
                atomic_reader_write(page_path, updated)
                cleaned.append(page_path.resolve())
    return cleaned


def remove_retired_taste_routes_from_html(html_text: str) -> str:
    return re.sub(
        r'\s*<a\s+href="\.\./taste-app\.html"[^>]*>\s*品味誌\s*</a>\s*',
        "\n    ",
        html_text,
        flags=re.IGNORECASE,
    )


def unlink_with_retry(path: Path, attempts: int = 3) -> None:
    for attempt in range(1, attempts + 1):
        try:
            path.unlink()
            return
        except PermissionError:
            if attempt == attempts:
                raise
            time.sleep(0.15)




def render_portal_markdown(entries: list[dict[str, object]], portal: Path, vault: Path | None = None) -> str:
    try:
        from .thinking_archive import portal_links
    except ImportError:
        from thinking_archive import portal_links
    thinking_links = portal_links(vault, portal) if vault is not None else ''
    generated_at = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %z")
    source_count = len(entries)
    return f"""---
type: "outcome_portal"
title: "成果頁總覽"
generated_at: "{generated_at}"
---

# 成果頁總覽

這是本專案的日常使用說明與閱讀入口。每一頁由 Obsidian source note、APP note、知識點與資訊圖生成；HTML 只是補充閱讀門面，不是新的真源。

## 立即開始

| 你想做甚麼 | 直接入口 | 會得到甚麼 |
|---|---|---|
| **閱讀所有已入庫內容** | [▣ 打開 HTML 知識成果總覽](outcome-pages/index.html) | 最新成果清單、搜尋／分頁入口，以及每條內容的成果頁、原文、應用和資訊圖 |
| **找回問答與思考地圖** | [打開思考紀錄目錄](outcome-pages/thinking.html) | 完整論證、互動思考地圖與來源筆記 |
| **查找知識成果** | [打開知識成果目錄](outcome-pages/knowledge.html) | 搜尋及分頁閱讀已有來源成果 |

## 這個系統幫你做甚麼

把一條值得保留的公開內容、你提供的 Markdown／文字／文檔，或一張 PNG／JPEG／WebP 圖片，整理成可回看的個人知識成果：保留來源和必要原文／原圖、抽取知識點、記錄可行應用、製作資訊圖，並在需要時把它扣連到既有知識與品味脈絡。所有成果都可由本頁讀回。

## 先從這裡開始

- **閱讀已有成果：** 按上方的「HTML 知識成果總覽」，再從清單開啟每條內容的「成果頁」；需要深入時，可在該頁開啟「原文」、「應用」或「資訊圖」。目前共有 {source_count} 條正式成果，清單只保留在 HTML 總覽，避免本頁重複和過長。
- **把新內容正式入庫：** 在 AI Terminal 說「入庫：&lt;URL&gt;」，或貼上／指定一份 Markdown、文字或文檔。若要保留清理後的全文，請明說「全文入庫」或「原文入庫」。接單後可繼續其他工作；完成時會回報成果頁網址。

## 日常操作：去哪裡、怎樣做、會得到甚麼

| 你的目的 | 入口與操作 | 完成後會得到甚麼 |
|---|---|---|
| 想快速了解一篇已入庫內容 | 在 HTML 知識成果總覽選取內容，再開啟「成果頁」 | 一頁式重點、採納判斷、品味與複利扣連、相關筆記和資訊圖入口 |
| 想讀來源或核對內容 | 由該成果頁開啟「原文」 | 來源身分、原文／正文保存、來源摘要與可追查證據 |
| 想知道如何把內容用在自己身上 | 由該成果頁開啟「應用」 | 個人化應用、下一步、複利判斷和已核對的扣連 |
| 想看圖像版重點 | 由該成果頁開啟「資訊圖」 | 已驗收的來源資訊圖；它解釋原文，不取代原文筆記 |
| 想新增一篇知識 | 在 AI Terminal 交代「入庫：網址」，或提供 Markdown／文字／文檔／一張圖片 | 同一正式流程會建立來源筆記、應用筆記、知識點、資訊圖和成果頁；圖片會另保存原圖、完整視覺解讀和校對證據；相同來源會去重，不會重複建立 |

## 現時可以做甚麼

- **知識閱讀：** 每個已驗證成果可由成果頁回到原文筆記、應用筆記、知識點及資訊圖；這張表提供全部正式成果的入口。
- **單篇入庫：** 支援一條公開 URL、一份使用者提供的 Markdown／文字／文檔，或一張 PNG／JPEG／WebP 圖片；它們使用同一套來源身分、去重、分析、資訊圖、Obsidian 寫入與讀回流程。圖片另要求原圖保存、`IMG-*` 視覺錨點覆蓋與第二輪校對。
- **原文保存：** 一般入庫會保存可驗證的來源分析與證據；明確要求「原文／全文／完整內容」時，會以完整正文模式保存清理後的文章主體。
- **知識扣連：** 內容可標示是否帶來加深、連接、行動、補底或修正等複利價值；「有洞見」必須附上可讀的洞見摘要和實際扣連，否則不能當作完成。

## 使用時請留意

- 「已入第二大腦」只在來源、應用、知識點、資訊圖與成果頁均完成正式讀回後才會顯示。
- 「待審」、「需脈絡」或「尚未完成」表示仍未完成，請按成果頁顯示的下一步補足資料。
- 本頁和 HTML 總覽是閱讀地圖；來源筆記、應用筆記、知識點與正式資料紀錄才是可驗證的資料真源。

- 生成時間: {generated_at}
{thinking_links}
"""


def relative_url(target: Path, base_dir: Path) -> str:
    try:
        return target.resolve().relative_to(base_dir.resolve()).as_posix()
    except ValueError:
        import os

        return Path(os.path.relpath(target.resolve(), base_dir.resolve())).as_posix()


if __name__ == "__main__":
    raise SystemExit(main())
