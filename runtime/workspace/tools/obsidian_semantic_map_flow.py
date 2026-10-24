"""Build and verify reusable Obsidian semantic maps for the 2nd Brain vault.

The flow is intentionally separate from the vault writer. It renders only
knowledge-network map notes, checks note-only wiki links, and keeps repo
governance, tools, logs, and implementation sources out of the graph.
"""

from __future__ import annotations

import argparse
import json
import os
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

try:
    from obsidian_vault_adapter import (
        application_link_for_source,
        concept_note_path,
        concept_title_from_knowledge_point,
        frontmatter_fields,
        is_generated_concept_note,
        plain_record_id,
        read_relation_registry,
        render_concept_note,
        safe_note_stem,
        write_graph_settings,
        build_reusable_concept_entries,
        concept_is_active,
    )
except ModuleNotFoundError:
    from tools.obsidian_vault_adapter import (  # type: ignore
        application_link_for_source,
        concept_note_path,
        concept_title_from_knowledge_point,
        frontmatter_fields,
        is_generated_concept_note,
        plain_record_id,
        read_relation_registry,
        render_concept_note,
        safe_note_stem,
        write_graph_settings,
        build_reusable_concept_entries,
        concept_is_active,
    )

try:
    from graph_preset import graph_settings_status
except ModuleNotFoundError:
    from tools.graph_preset import graph_settings_status  # type: ignore


DEFAULT_VAULT = Path(os.environ.get("CREATIVE_SECOND_BRAIN_VAULT", "knowledge-base-vault"))
DEFAULT_MAP_ROOT = DEFAULT_VAULT / "maps"
TOOL_PATH = "tools/obsidian_semantic_map_flow.py"
NOTE_LINK_RE = re.compile(r"(?<!!)\[\[([^\]#|]+)(?:[#|][^\]]*)?\]\]")
MACHINE_ID_LINK_RE = re.compile(r"(?<!!)\[\[((?:KP-|KS-|APP-|SRC-|REL-)[^\]#|]+)(?:[#|][^\]]*)?\]\]")
UPDATED_RE = re.compile(r'^updated:\s*"?([^"\n]+)"?', re.MULTILINE)


@dataclass(frozen=True)
class InventoryItem:
    path: str
    source_class: str
    role: str
    first_read: str


@dataclass(frozen=True)
class DirectoryItem:
    title: str
    path: str
    source_class: str
    purpose: str
    links: tuple[str, ...]
    notes: tuple[str, ...]


@dataclass(frozen=True)
class IdeaBridgeNote:
    title: str
    idea_type: str
    role: str
    question: str
    one_liner: str
    sections: tuple[tuple[str, tuple[str, ...]], ...]
    source_records: tuple[str, ...]
    curation_status: str = ""
    curated_on: str = ""
    curation_note: str = ""


GRAPH_ITEMS = (
    InventoryItem(os.environ.get("CREATIVE_SECOND_BRAIN_VAULT", "knowledge-base-vault") + "sources/", "knowledge_source_layer", "原始來源與來源稽核", "由目錄索引讀起，不逐檔放入 graph"),
    InventoryItem(os.environ.get("CREATIVE_SECOND_BRAIN_VAULT", "knowledge-base-vault") + "applications/", "application_layer", "採納、應用與個人化判斷", "用來連接知識與實際使用"),
    InventoryItem(os.environ.get("CREATIVE_SECOND_BRAIN_VAULT", "knowledge-base-vault") + "knowledge-points/", "knowledge_point_record_layer", "知識點記錄層", "作查證記錄，不用機器 ID wikilink 進 graph"),
    InventoryItem(os.environ.get("CREATIVE_SECOND_BRAIN_VAULT", "knowledge-base-vault") + "concepts/", "concept_layer", "語義概念層", "Obsidian graph 主視圖"),
    InventoryItem(os.environ.get("CREATIVE_SECOND_BRAIN_VAULT", "knowledge-base-vault") + "maps/", "map_layer", "人用入口地圖", "由總索引和分層 map 進入"),
    InventoryItem(os.environ.get("CREATIVE_SECOND_BRAIN_VAULT", "knowledge-base-vault") + "outcome-pages/", "presentation_layer", "成果頁呈現層", "作呈現出口，不逐頁入 graph"),
)
GRAPH_EXCLUDED_ITEMS = (
    InventoryItem("AGENTS.md", "graph_excluded_workflow", "Agent 操作規則", "不進 Obsidian graph"),
    InventoryItem("dev/", "graph_excluded_workflow", "Agent 接力、規則包、索引、驗收與遷移資料", "不進 Obsidian graph"),
    InventoryItem("docs/", "graph_excluded_project_docs", "本專案內部規格與說明", "不進 Obsidian graph"),
    InventoryItem("tools/", "graph_excluded_tools", "本專案工具程式", "不進 Obsidian graph"),
    InventoryItem("outputs/", "graph_excluded_outputs", "執行輸出與暫存結果", "不進 Obsidian graph"),
)
PREFERRED_CONCEPTS = ()
IDEA_BRIDGE_NOTES = ()

SUSPICIOUS_CONCEPT_ENDINGS = (
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
SUSPICIOUS_ENGLISH_ENDINGS = ("provide", "plan", "clean work")
INTERNAL_REPO_TERMS = ("AGENTS.md", "PROJECT_INDEX", "SESSION_HANDOFF", "SESSION_LOG", "validation log")
CORE_MAP_FILES = (
    "總索引.md",
    "File Index.md",
    "知識點 - 知識庫語義索引.md",
)
SCAN_EXCLUDED_DIR_NAMES = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    "__pycache__",
    "node_modules",
    "_tmp_adapter_tests",
}
SCAN_EXCLUDED_PREFIXES = ("validation_manifest_",)


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate and verify Obsidian semantic map drafts.")
    parser.add_argument("--vault", type=Path, default=DEFAULT_VAULT)
    parser.add_argument("--map-root", type=Path, default=DEFAULT_MAP_ROOT)
    parser.add_argument("--updated-date", default="", help="YYYY-MM-DD. Defaults to existing map date, then today.")
    parser.add_argument("--write", action="store_true", help="Write generated map notes and idea bridge notes.")
    parser.add_argument("--write-missing-concepts", action="store_true", help="Write only missing concept notes. Existing concept notes are left untouched.")
    parser.add_argument("--rewrite-concepts", action="store_true", help="Rewrite generated concept notes from registry and prune stale generated concept titles.")
    parser.add_argument("--write-graph-settings", action="store_true", help="Write Obsidian graph settings for the concept-only semantic graph.")
    parser.add_argument("--prune-stale-maps", action="store_true", help="Delete stale map notes generated by this tool but no longer planned.")
    parser.add_argument("--check", action="store_true", help="Fail when generated map files are missing, drifted, or invalid.")
    parser.add_argument("--quality-limit", type=int, default=50, help="Maximum suspicious concept titles to include in JSON output. Use 0 for all.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable output.")
    args = parser.parse_args()

    root = Path.cwd().resolve()
    vault = resolve_inside(root, args.vault)
    map_root = resolve_inside(root, args.map_root)
    updated = args.updated_date or existing_updated_date(map_root) or date.today().isoformat()

    concept_write_result: dict[str, object] = {"enabled": False, "written_count": 0, "written_files": [], "pruned_count": 0, "pruned_files": []}
    if args.write_missing_concepts or args.rewrite_concepts:
        concept_write_result = write_concept_note_updates(root, vault, updated, rewrite=args.rewrite_concepts)

    drafts = build_map_drafts(root, vault, map_root, updated)
    drafts.update(build_idea_bridge_drafts(vault, updated))
    inventory = build_inventory(root)
    graph_exclusions_and_gaps = build_graph_exclusions_and_gaps(root, vault)
    concept_title_quality = concept_title_quality_report(vault, max(0, args.quality_limit))
    semantic_graph_health = concept_graph_connectivity_report(vault)
    link_checks = {
        "vault_note_links": wiki_link_check(vault, drafts, scope="vault"),
        "maps_note_links": wiki_link_check(vault, drafts, scope="maps"),
    }
    machine_link_issues = machine_id_link_issues(vault, drafts)
    file_status = compare_generated_files(root, drafts)
    graph_settings = graph_settings_status(vault)
    written: list[str] = []
    pruned: list[str] = []

    if args.write:
        written = write_drafts(root, drafts)
        if args.prune_stale_maps:
            pruned = prune_stale_generated_maps(root, map_root, drafts)
        inventory = build_inventory(root)
        graph_exclusions_and_gaps = build_graph_exclusions_and_gaps(root, vault)
        file_status = compare_generated_files(root, drafts)
        concept_title_quality = concept_title_quality_report(vault, max(0, args.quality_limit))
        semantic_graph_health = concept_graph_connectivity_report(vault)
        link_checks = {
            "vault_note_links": wiki_link_check(vault, drafts, scope="vault"),
            "maps_note_links": wiki_link_check(vault, drafts, scope="maps"),
        }
        machine_link_issues = machine_id_link_issues(vault, drafts)
    if args.write or args.write_graph_settings:
        write_graph_settings(vault)
        graph_settings = graph_settings_status(vault)

    status = classify_status(args, file_status, link_checks, machine_link_issues, graph_settings)
    result: dict[str, object] = {
        "mode": "obsidian_semantic_map_flow",
        "status": status,
        "write": args.write,
        "check": args.check,
        "updated": updated,
        "inventory": inventory,
        "planned_files": [relative_to_root(root, path) for path in sorted(drafts)],
        "file_status": file_status,
        "written_files": written,
        "pruned_files": pruned,
        "concept_write_result": concept_write_result,
        "wiki_link_checks": link_checks,
        "machine_id_wikilink_issues": machine_link_issues,
        "graph_settings": graph_settings,
        "graph_exclusions_and_gaps": graph_exclusions_and_gaps,
        "concept_title_quality": concept_title_quality,
        "semantic_graph_health": semantic_graph_health,
        "commands": {
            "dry_run": f"python {TOOL_PATH} --json",
            "write": f"python {TOOL_PATH} --write --prune-stale-maps --json",
            "write_idea_bridges": f"python {TOOL_PATH} --write --check --json",
            "write_missing_concepts": f"python {TOOL_PATH} --write-missing-concepts --check --json",
            "rewrite_concepts": f"python {TOOL_PATH} --rewrite-concepts --check --json",
            "write_graph_settings": f"python {TOOL_PATH} --write-graph-settings --check --json",
            "check": f"python {TOOL_PATH} --check --json",
            "concept_title_quality": f"python {TOOL_PATH} --check --quality-limit 9999 --json",
        },
    }
    print_result(result, json_mode=args.json)
    return 1 if args.check and status != "ready" else 0


def resolve_inside(root: Path, path: Path) -> Path:
    candidate = path if path.is_absolute() else root / path
    resolved = candidate.resolve()
    if resolved != root and root not in resolved.parents:
        raise SystemExit(f"path outside workspace: {path}")
    return resolved


def existing_updated_date(map_root: Path) -> str:
    target = map_root / "總索引.md"
    if not target.exists():
        return ""
    match = UPDATED_RE.search(target.read_text(encoding="utf-8-sig"))
    return match.group(1).strip() if match else ""


def build_inventory(root: Path) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for item in (*GRAPH_ITEMS, *GRAPH_EXCLUDED_ITEMS):
        target = root / item.path.rstrip("/")
        row: dict[str, object] = {
            "path": item.path,
            "source_class": item.source_class,
            "role": item.role,
            "first_read": item.first_read,
            "graph_action": "include" if not item.source_class.startswith("graph_excluded") else "exclude",
            "exists": target.exists(),
        }
        if target.exists():
            row.update(scan_path(target))
        rows.append(row)
    return rows


def scan_path(target: Path) -> dict[str, object]:
    if target.is_file():
        return {"kind": "file", "file_count": 1, "markdown_count": 1 if target.suffix.casefold() == ".md" else 0, "scan_errors": []}
    file_count = 0
    markdown_count = 0
    dir_count = 0
    errors: list[str] = []
    skipped_dirs: list[str] = []

    def onerror(error: OSError) -> None:
        errors.append(str(error))

    for dirpath, dirnames, filenames in os.walk(target, onerror=onerror):
        kept_dirnames = []
        for dirname in dirnames:
            if should_skip_scan_dir(dirname):
                skipped_dirs.append(str((Path(dirpath) / dirname).relative_to(target)))
            else:
                kept_dirnames.append(dirname)
        dirnames[:] = kept_dirnames
        dir_count += len(dirnames)
        file_count += len(filenames)
        markdown_count += sum(1 for name in filenames if name.casefold().endswith(".md"))
    return {
        "kind": "directory",
        "file_count": file_count,
        "markdown_count": markdown_count,
        "directory_count": dir_count,
        "scan_errors": errors,
        "skipped_dirs": skipped_dirs[:20],
        "skipped_dir_count": len(skipped_dirs),
    }


def should_skip_scan_dir(dirname: str) -> bool:
    return dirname in SCAN_EXCLUDED_DIR_NAMES or any(dirname.startswith(prefix) for prefix in SCAN_EXCLUDED_PREFIXES)


def build_graph_exclusions_and_gaps(root: Path, vault: Path) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = [
        {
            "type": "graph_excluded",
            "path": "AGENTS.md",
            "reason": "本專案內部 Agent 操作規則，不進知識網或品味網 graph",
            "exists": (root / "AGENTS.md").exists(),
        },
        {
            "type": "graph_excluded",
            "path": "docs/",
            "reason": "本專案內部規格與說明，不進 2nd Brain graph",
            "exists": (root / "docs").exists(),
        },
        {
            "type": "graph_excluded",
            "path": "dev/",
            "reason": "本專案內部治理與接力狀態，不進 2nd Brain graph",
            "exists": (root / "dev").exists(),
        },
        {
            "type": "graph_excluded",
            "path": "tools/",
            "reason": "本專案工具程式，不進 2nd Brain graph",
            "exists": (root / "tools").exists(),
        },
        {
            "type": "graph_excluded",
            "path": "outputs/",
            "reason": "執行輸出與暫存結果，不進 2nd Brain graph",
            "exists": (root / "outputs").exists(),
        },
    ]
    missing_concepts = missing_concept_notes(vault)
    rows.append(
        {
            "type": "known_gap",
            "path": os.environ.get("CREATIVE_SECOND_BRAIN_VAULT", "knowledge-base-vault") + "concepts/",
            "reason": "knowledge-point registry 未全部投影到 concept notes",
            "missing_count": len(missing_concepts),
            "examples": missing_concepts[:10],
        }
    )
    rows.append(
        {
            "type": "known_gap",
            "path": os.environ.get("CREATIVE_SECOND_BRAIN_VAULT", "knowledge-base-vault") + "concepts/",
            "reason": "首批 idea bridge 已落地，但 graph health 仍顯示多個 source-local 小團；下一批應按 orphan、過小 component、缺 hub / tension / taste signal 的主題繼續整理，不應把首批 MVP 說成已完成知識網",
        }
    )
    return rows


def concept_title_quality_report(vault: Path, limit: int) -> dict[str, object]:
    concept_dir = vault / "concepts"
    if not concept_dir.exists():
        return {"checked_count": 0, "suspicious_count": 0, "reported_count": 0, "suspicious": [], "report_only": True}

    suspicious: list[dict[str, object]] = []
    checked_count = 0
    for path in sorted(concept_dir.glob("*.md")):
        checked_count += 1
        fields = frontmatter_fields(path)
        title = str(fields.get("concept") or fields.get("title") or path.stem).strip()
        reasons = suspicious_concept_title_reasons(title)
        if reasons:
            suspicious.append(
                {
                    "file": path.relative_to(vault).as_posix(),
                    "title": title,
                    "reasons": reasons,
                }
            )

    reported = suspicious if limit == 0 else suspicious[:limit]
    return {
        "checked_count": checked_count,
        "suspicious_count": len(suspicious),
        "reported_count": len(reported),
        "suspicious": reported,
        "report_only": True,
    }


def concept_graph_connectivity_report(vault: Path) -> dict[str, object]:
    concept_dir = vault / "concepts"
    if not concept_dir.exists():
        return {
            "checked_count": 0,
            "edge_count": 0,
            "linked_node_count": 0,
            "orphan_count": 0,
            "component_count": 0,
            "largest_component_size": 0,
            "multi_source_component_count": 0,
            "status": "missing_concept_dir",
            "report_only": True,
        }

    concept_files = sorted(concept_dir.glob("*.md"))
    stems = {path.stem for path in concept_files}
    adjacency: dict[str, set[str]] = {path.stem: set() for path in concept_files}
    source_records_by_stem: dict[str, set[str]] = {}

    for path in concept_files:
        stem = path.stem
        fields = frontmatter_fields(path)
        source_records_by_stem[stem] = frontmatter_string_set(fields.get("source_records"))
        text = path.read_text(encoding="utf-8-sig")
        for target in NOTE_LINK_RE.findall(text):
            if target not in stems or target == stem:
                continue
            adjacency[stem].add(target)
            adjacency[target].add(stem)

    edge_count = sum(len(targets) for targets in adjacency.values()) // 2
    linked_node_count = sum(1 for targets in adjacency.values() if targets)
    orphan_nodes = sorted(stem for stem, targets in adjacency.items() if not targets)
    components = connected_components(adjacency)
    component_rows: list[dict[str, object]] = []
    for component in components:
        sources: set[str] = set()
        for stem in component:
            sources.update(source_records_by_stem.get(stem, set()))
        component_rows.append(
            {
                "size": len(component),
                "source_record_count": len(sources),
                "sample": sorted(component)[:8],
            }
        )
    component_rows.sort(key=lambda row: (-int(row["size"]), -int(row["source_record_count"]), str(row["sample"])))
    multi_source_component_count = sum(1 for row in component_rows if int(row["source_record_count"]) >= 2)
    needs_bridges = len(component_rows) > 12 or (component_rows and int(component_rows[0]["size"]) < 12)
    return {
        "checked_count": len(concept_files),
        "edge_count": edge_count,
        "linked_node_count": linked_node_count,
        "orphan_count": len(orphan_nodes),
        "orphan_examples": orphan_nodes[:20],
        "component_count": len(component_rows),
        "largest_component_size": int(component_rows[0]["size"]) if component_rows else 0,
        "largest_components": component_rows[:10],
        "multi_source_component_count": multi_source_component_count,
        "status": "needs_semantic_bridges" if needs_bridges else "usable_concept_graph",
        "interpretation": "這是只讀健康報告；它不代表 link check 失敗。若 component 多且最大群細，代表主圖乾淨但跨來源語義橋仍不足。",
        "report_only": True,
    }


def frontmatter_string_set(value: object) -> set[str]:
    if isinstance(value, list):
        return {str(item).strip() for item in value if str(item).strip()}
    if isinstance(value, str) and value.strip():
        return {value.strip()}
    return set()


def connected_components(adjacency: dict[str, set[str]]) -> list[set[str]]:
    components: list[set[str]] = []
    seen: set[str] = set()
    for node in sorted(adjacency):
        if node in seen:
            continue
        stack = [node]
        component: set[str] = set()
        while stack:
            current = stack.pop()
            if current in seen:
                continue
            seen.add(current)
            component.add(current)
            stack.extend(sorted(adjacency[current] - seen))
        components.append(component)
    return components


def suspicious_concept_title_reasons(title: str) -> list[str]:
    reasons: list[str] = []
    if len(title) <= 3:
        reasons.append("標題太短，可能不是完整概念")
    if len(title) > 48:
        reasons.append("標題過長，可能仍是原句而非概念名")
    if title.endswith(SUSPICIOUS_CONCEPT_ENDINGS):
        reasons.append("句尾像被截斷")
    if title.casefold().endswith(SUSPICIOUS_ENGLISH_ENDINGS):
        reasons.append("英文句尾像被截斷")
    if ";" in title or "；" in title:
        reasons.append("包含分號，可能混合多個概念")
    if re.search(r"\b[\w.-]+\.md\b", title):
        reasons.append("標題含檔名，可能是 repo 內部文件線索")
    if any(term in title for term in INTERNAL_REPO_TERMS):
        reasons.append("含 repo 內部治理檔名，需確認是否適合知識網")
    return reasons


def write_concept_note_updates(root: Path, vault: Path, updated: str, *, rewrite: bool) -> dict[str, object]:
    entries, kp_uid_to_concept = build_concept_entries(vault)
    add_related_concept_links(vault, entries, kp_uid_to_concept)
    written: list[str] = []
    pruned: list[str] = []
    for path, entry in sorted(entries.items()):
        rendered = render_concept_note(entry, updated)
        existing = path.read_text(encoding="utf-8-sig") if path.exists() else ""
        if existing == rendered:
            continue
        if path.exists() and not rewrite:
            continue
        if path.exists() and (not is_generated_concept_note(path) or not concept_is_active(frontmatter_fields(path))):
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(rendered, encoding="utf-8", newline="\n")
        written.append(relative_to_root(root, path))
    # Rebuild is not authority to delete obsolete notes. Reviewed source-local
    # withdrawal belongs to the adapter's same-path content revision owner.
    return {
        "enabled": True,
        "rewrite": rewrite,
        "written_count": len(written),
        "written_files": written,
        "pruned_count": len(pruned),
        "pruned_files": pruned,
    }


def build_concept_entries(vault: Path) -> tuple[dict[Path, dict[str, object]], dict[str, Path]]:
    return build_reusable_concept_entries(vault)


def add_related_concept_links(vault: Path, entries: dict[Path, dict[str, object]], kp_uid_to_concept: dict[str, Path]) -> None:
    # Source-local relations (including linked ones) are not idea bridges.
    # Their evidence remains available from KP/APP/material, never promoted here.
    return None


def missing_concept_notes(vault: Path) -> list[dict[str, str]]:
    missing: list[dict[str, str]] = []
    entries, _ = build_reusable_concept_entries(vault)
    for concept_path, entry in entries.items():
        if not concept_path.exists():
            missing.append(
                {
                    "expected": concept_path.relative_to(vault).as_posix(),
                    "kp_note": next(iter(sorted(entry["knowledge_point_records"])), ""),
                }
            )
    return missing


def build_map_drafts(root: Path, vault: Path, map_root: Path, updated: str) -> dict[Path, str]:
    concept_links = current_concept_links(vault)
    directories = directory_items()
    drafts: dict[Path, str] = {
        map_root / "總索引.md": render_total_index(updated),
        map_root / "File Index.md": render_file_index(updated, root),
        map_root / "知識點 - 知識庫語義索引.md": render_knowledge_point_index(updated, concept_links),
    }
    for item in directories:
        drafts[map_root / "directories" / f"{item.title}.md"] = render_directory_index(item, updated)
    return drafts


def build_idea_bridge_drafts(vault: Path, updated: str) -> dict[Path, str]:
    concept_dir = vault / "concepts"
    drafts: dict[Path, str] = {}
    for note in IDEA_BRIDGE_NOTES:
        path = concept_dir / f"{safe_note_stem(note.title)}.md"
        drafts[path] = render_idea_bridge_note(note, updated)
    return drafts


def current_concept_links(vault: Path) -> list[str]:
    concept_dir = vault / "concepts"
    if not concept_dir.exists():
        return []
    return [path.stem for path in sorted(concept_dir.glob("*.md"), key=lambda path: path.stem.casefold())
            if concept_is_active(frontmatter_fields(path))][:24]


def directory_items() -> tuple[DirectoryItem, ...]:
    return (
        DirectoryItem(
            "目錄索引 - knowledge-base-vault",
            os.environ.get("CREATIVE_SECOND_BRAIN_VAULT", "knowledge-base-vault") + "",
            "knowledge_vault",
            "`筆記庫/` 是 2nd Brain 的 Obsidian vault 內容位置。",
            (
                "總索引",
                "File Index",
                "知識點 - 知識庫語義索引",
                "目錄索引 - sources 原始來源層",
                "目錄索引 - applications 採納應用層",
                "目錄索引 - knowledge-points 知識點記錄層",
                "目錄索引 - concepts 語義概念層",
            ),
            (
                "保存 source、application、knowledge-point、concept、outcome、maps 等 vault 內容。",
                "record 層維持查證身份；concept、semantic bridge 與 hub 提供人可讀語義入口。",
                "本索引不把 repo 內部治理文檔、工具或開發真源做成 graph 節點。",
            ),
        ),
        DirectoryItem(
            "目錄索引 - sources 原始來源層",
            os.environ.get("CREATIVE_SECOND_BRAIN_VAULT", "knowledge-base-vault") + "sources/",
            "knowledge_source_layer",
            "`sources/` 保存原始來源筆記、來源稽核、擷取內容與資訊圖鎖定稿。",
            ("File Index", "知識點 - 知識庫語義索引", "目錄索引 - knowledge-base-vault"),
            ("來源筆記是查證入口，不直接用機器 ID wikilink 形成 graph 主幹。", "需要追溯原文時由 concept 或 search 回到 source record。"),
        ),
        DirectoryItem(
            "目錄索引 - applications 採納應用層",
            os.environ.get("CREATIVE_SECOND_BRAIN_VAULT", "knowledge-base-vault") + "applications/",
            "application_layer",
            "`applications/` 保存採納、應用、個人化與後續行動判斷。",
            ("File Index", "目錄索引 - knowledge-base-vault"),
            ("這一層回答知識是否值得留下及怎樣使用。", "它是跨來源語義橋的主要候選材料，但正式 graph link 仍須回到 concept 層。"),
        ),
        DirectoryItem(
            "目錄索引 - knowledge-points 知識點記錄層",
            os.environ.get("CREATIVE_SECOND_BRAIN_VAULT", "knowledge-base-vault") + "knowledge-points/",
            "knowledge_point_record_layer",
            "`knowledge-points/` 保存每個來源拆出的 atomic knowledge point 記錄。",
            ("File Index", "知識點 - 知識庫語義索引", "目錄索引 - concepts 語義概念層"),
            ("這一層是 registry 可追蹤記錄，不把 `KP-...` 變成 graph 主要節點。", "概念層才是人看的語義 graph 主體。"),
        ),
        DirectoryItem(
            "目錄索引 - concepts 語義概念層",
            os.environ.get("CREATIVE_SECOND_BRAIN_VAULT", "knowledge-base-vault") + "concepts/",
            "concept_layer",
            "`concepts/` 保存人可閱讀、可追問的語義概念節點。",
            ("File Index", "知識點 - 知識庫語義索引"),
            ("Obsidian graph 預設應主要看 concept notes。", "concept notes 只 wikilink 到其他 concept notes；主題 hub 也是概念節點，不是記錄 ID。", "跨來源橋接不足時，graph 會乾淨但分散，不能稱為已完成的 2nd Brain 知識網。"),
        ),
    )


def frontmatter(note_type: str, title: str, updated: str, extra: dict[str, str] | None = None) -> str:
    fields = {
        "type": note_type,
        "title": title,
        "generated_by": TOOL_PATH,
        "updated": updated,
    }
    if extra:
        fields.update(extra)
    lines = ["---"]
    for key, value in fields.items():
        lines.append(f'{key}: "{value}"')
    lines.append("---")
    return "\n".join(lines) + "\n\n"


def idea_frontmatter(note: IdeaBridgeNote, updated: str) -> str:
    fields: dict[str, str | tuple[str, ...]] = {
        "type": "idea",
        "concept": note.title,
        "title": note.title,
        "idea_type": note.idea_type,
        "graph_group": note.idea_type,
        "concept_status": "graph_node",
        "graph_layer": "idea_layer",
        "graph_scope": "idea_links_only",
        "graph_role": note.role,
        "graph_question": note.question,
        "generated_by": TOOL_PATH,
        "updated": updated,
        "source_records": note.source_records,
    }
    if note.curation_status:
        fields["curation_status"] = note.curation_status
    if note.curated_on:
        fields["curated_on"] = note.curated_on
    lines = ["---"]
    for key, value in fields.items():
        if isinstance(value, tuple):
            lines.append(f"{key}:")
            for item in value:
                lines.append(f"  - {json.dumps(item, ensure_ascii=False)}")
        else:
            lines.append(f"{key}: {json.dumps(value, ensure_ascii=False)}")
    lines.append("---")
    return "\n".join(lines) + "\n\n"


def render_idea_bridge_note(note: IdeaBridgeNote, updated: str) -> str:
    body = idea_frontmatter(note, updated)
    body += f"# {note.title}\n\n"
    body += "## 一句話\n\n"
    body += f"{note.one_liner}\n\n"
    body += "## 圖譜用途\n\n"
    body += f"- 類型：{note.idea_type}\n"
    body += f"- 角色：{note.role}\n"
    body += f"- 可追問：{note.question}\n\n"
    if note.curation_note:
        body += "## 整理判斷\n\n"
        body += f"- 狀態：{note.curation_status}\n"
        body += f"- 整理日期：{note.curated_on}\n"
        body += f"- 邊界：{note.curation_note}\n\n"
    for heading, links in note.sections:
        body += f"## {heading}\n\n"
        for link in links:
            body += f"- [[{link}]]\n"
        body += "\n"
    body += "## 查證來源\n\n"
    body += "以下只作 record 身份與查證線索，不作 Obsidian graph 主節點。\n\n"
    for record in note.source_records:
        body += f"- `{record}`\n"
    body += "\n## 整理規則\n\n"
    body += "- 若新增來源只支持單篇材料內的小概念，先不要硬連；每週整理時再判斷是否連到本 note 或需要拆出新主題、取捨或應用場景。\n"
    body += "- 若本 note 變成雜項收納，應拆成更清楚的 idea note，並保留可查證 record ID。\n"
    return body


def render_total_index(updated: str) -> str:
    body = frontmatter("map_index", "總索引", updated, {"layer": "semantic_navigation", "status": "generated_sample"})
    body += """# 總索引

這是 2nd Brain Obsidian graph 的入口。它只連知識網與 vault 內容分支，不處理本專案內部治理、工具或開發真源。

真正有用的圖不是把每篇來源各自變成一小團，而是由概念、跨來源語義橋、主題與應用場景串起可重用的知識網。

## 主要入口

- [[知識點 - 知識庫語義索引]]
- [[File Index]]

## 首讀路徑

1. 先讀 [[知識點 - 知識庫語義索引]]，理解知識網怎樣由來源、知識點與概念組成。
2. 需要找 vault 內容位置時才讀 [[File Index]]。

## 邊界

本索引不把 repo 內部 governance、Agent handoff、tools、tests、outputs、project docs 或實作真源變成 Obsidian graph 節點。
"""
    return body


def render_file_index(updated: str, root: Path) -> str:
    body = frontmatter("file_index", "File Index", updated, {"layer": "semantic_navigation", "status": "generated_sample"})
    body += """# File Index

這是 2nd Brain vault 的小型 file inventory。它只把知識網相關位置放入 graph 導航；repo 內部治理、工具與輸出只列為排除項。

## Graph 內容入口

| 範圍 | 類型 | 狀態 | 首讀建議 |
| --- | --- | --- | --- |
"""
    for item in GRAPH_ITEMS:
        exists = "存在" if (root / item.path.rstrip("/")).exists() else "缺失"
        body += f"| `{item.path}` | {item.source_class} | {exists} | {item.first_read} |\n"
    body += """
## 目錄索引

- [[目錄索引 - knowledge-base-vault]]
- [[目錄索引 - sources 原始來源層]]
- [[目錄索引 - applications 採納應用層]]
- [[目錄索引 - knowledge-points 知識點記錄層]]
- [[目錄索引 - concepts 語義概念層]]

## 分支入口

- [[知識點 - 知識庫語義索引]]

## 不入 Graph

| 範圍 | 原因 |
| --- | --- |
"""
    for item in GRAPH_EXCLUDED_ITEMS:
        exists = "存在" if (root / item.path.rstrip("/")).exists() else "缺失"
        body += f"| `{item.path}` | {exists}；{item.first_read} |\n"
    body += """
"""
    return body


def render_knowledge_point_index(updated: str, concept_links: list[str]) -> str:
    body = frontmatter("knowledge_point", "知識點 - 知識庫語義索引", updated, {"layer": "semantic_navigation", "status": "generated_sample"})
    body += """# 知識點 - 知識庫語義索引

這個入口描述知識網如何把來源、知識點、概念與採納判斷放入 Obsidian 語義圖。

核心判斷是：Obsidian graph 的節點應該是人能閱讀與追問的 Markdown note，不應該是 table row、欄位、機器 ID、repo 內部治理文件或工具檔。

第二層判斷是：concept note 不能只保留單篇來源內部關係。可用的知識網需要把不同來源重覆出現、互相支持、互相修正或可一起應用的概念，整理成有證據的跨來源語義橋。

## 相關概念

"""
    if concept_links:
        for concept in concept_links:
            body += f"- [[{concept}]]\n"
    else:
        body += "- 暫無已核實概念連結。\n"
    body += """
## 語義橋接方向

- 由 application note 的跨來源洞見、relation registry 與正式回饋，找出需要連接的概念。
- 只把有來源或應用證據的關係寫成 concept-to-concept link，不因同詞相似硬連。
- 優先建立主題 hub：一個 hub 應連到多個來源、多個概念及一個可追問問題。
- `KP-...`、`KS-...`、`APP-...` 只作記錄身份，不作 graph 主節點。

## 相關目錄

- [[目錄索引 - knowledge-base-vault]]
- [[目錄索引 - sources 原始來源層]]
- [[目錄索引 - knowledge-points 知識點記錄層]]
- [[目錄索引 - concepts 語義概念層]]
- [[目錄索引 - applications 採納應用層]]

## 不包括

Agent 接力、rules、handoff、validation log、tools、tests、outputs 與 repo 內部規格不進這個 graph 主幹。
"""
    return body


def render_directory_index(item: DirectoryItem, updated: str) -> str:
    body = frontmatter(
        "directory_index",
        item.title,
        updated,
        {"directory": item.path, "source_class": item.source_class, "status": "generated_sample"},
    )
    body += f"# {item.title}\n\n{item.purpose}\n\n## 相關入口\n\n"
    for link in item.links:
        body += f"- [[{link}]]\n"
    body += "\n## 摘要\n\n"
    for note in item.notes:
        body += f"- {note}\n"
    return body


def compare_generated_files(root: Path, drafts: dict[Path, str]) -> dict[str, object]:
    missing: list[str] = []
    drifted: list[str] = []
    up_to_date: list[str] = []
    for path, content in sorted(drafts.items()):
        rel = relative_to_root(root, path)
        if not path.exists():
            missing.append(rel)
            continue
        existing = path.read_text(encoding="utf-8-sig")
        if existing != content:
            drifted.append(rel)
        else:
            up_to_date.append(rel)
    return {"missing": missing, "drifted": drifted, "up_to_date": up_to_date}


def write_drafts(root: Path, drafts: dict[Path, str]) -> list[str]:
    written: list[str] = []
    for path, content in sorted(drafts.items()):
        path.parent.mkdir(parents=True, exist_ok=True)
        existing = path.read_text(encoding="utf-8-sig") if path.exists() else None
        if existing != content:
            path.write_text(content, encoding="utf-8", newline="\n")
            written.append(relative_to_root(root, path))
    return written


def prune_stale_generated_maps(root: Path, map_root: Path, drafts: dict[Path, str]) -> list[str]:
    planned = set(drafts)
    pruned: list[str] = []
    if not map_root.exists():
        return pruned
    for path in sorted(map_root.rglob("*.md")):
        if path in planned:
            continue
        text = path.read_text(encoding="utf-8-sig")
        if f'generated_by: "{TOOL_PATH}"' not in text:
            continue
        path.unlink()
        pruned.append(relative_to_root(root, path))
    return pruned


def wiki_link_check(vault: Path, drafts: dict[Path, str], scope: str) -> dict[str, object]:
    contents = markdown_contents(vault, drafts, scope)
    targets = note_targets(vault, drafts)
    links: list[dict[str, str]] = []
    missing: list[dict[str, str]] = []
    for path, text in contents.items():
        for target in NOTE_LINK_RE.findall(text):
            row = {"file": path.as_posix(), "target": target}
            links.append(row)
            if target not in targets:
                missing.append(row)
    return {
        "scope": scope,
        "files": len(contents),
        "total_links": len(links),
        "unique_targets": len({row["target"] for row in links}),
        "missing_count": len(missing),
        "missing": missing[:20],
    }


def markdown_contents(vault: Path, drafts: dict[Path, str], scope: str) -> dict[Path, str]:
    base = vault / "maps" if scope == "maps" else vault
    contents: dict[Path, str] = {}
    for path in sorted(base.rglob("*.md")):
        contents[path] = path.read_text(encoding="utf-8-sig")
    for path, content in drafts.items():
        if scope == "maps" and not is_relative_to(path, vault / "maps"):
            continue
        contents[path] = content
    return contents


def note_targets(vault: Path, drafts: dict[Path, str]) -> set[str]:
    targets: set[str] = set()
    for path in vault.rglob("*.md"):
        add_note_target(targets, vault, path)
    for path in drafts:
        add_note_target(targets, vault, path)
    return targets


def add_note_target(targets: set[str], vault: Path, path: Path) -> None:
    targets.add(path.stem)
    if is_relative_to(path, vault):
        targets.add(path.relative_to(vault).with_suffix("").as_posix())


def machine_id_link_issues(vault: Path, drafts: dict[Path, str]) -> list[dict[str, str]]:
    contents = markdown_contents(vault, drafts, scope="maps")
    concept_dir = vault / "concepts"
    if concept_dir.exists():
        for path in sorted(concept_dir.rglob("*.md")):
            contents[path] = path.read_text(encoding="utf-8-sig")
    for path, content in drafts.items():
        if is_relative_to(path, concept_dir):
            contents[path] = content
    issues: list[dict[str, str]] = []
    for path, text in contents.items():
        for target in MACHINE_ID_LINK_RE.findall(text):
            issues.append({"file": path.as_posix(), "target": target})
    return issues[:50]


def classify_status(
    args: argparse.Namespace,
    file_status: dict[str, object],
    link_checks: dict[str, dict[str, object]],
    machine_link_issues: list[dict[str, str]],
    graph_settings: dict[str, object],
) -> str:
    missing = bool(file_status["missing"])
    drifted = bool(file_status["drifted"])
    broken_links = any(int(check["missing_count"]) > 0 for check in link_checks.values())
    if broken_links or machine_link_issues:
        return "needs_review"
    if args.check and not bool(graph_settings.get("ready")):
        return "needs_write"
    if args.check and (missing or drifted):
        return "needs_write"
    if not args.write and (missing or drifted):
        return "dry_run_ready"
    return "ready"


def print_result(result: dict[str, object], json_mode: bool) -> None:
    if json_mode:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    print(f"status: {result['status']}")
    print(f"planned files: {len(result['planned_files'])}")  # type: ignore[arg-type]
    written_files = result["written_files"]
    print(f"written files: {len(written_files)}")  # type: ignore[arg-type]
    checks = result["wiki_link_checks"]  # type: ignore[assignment]
    for name, check in checks.items():
        print(f"{name}: missing={check['missing_count']} total_links={check['total_links']}")


def relative_to_root(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix() if is_relative_to(path, root) else path.as_posix()


def is_relative_to(path: Path, base: Path) -> bool:
    try:
        path.relative_to(base)
        return True
    except ValueError:
        return False


if __name__ == "__main__":
    raise SystemExit(main())
