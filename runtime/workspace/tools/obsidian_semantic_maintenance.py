"""Create repeatable maintenance receipts for the Obsidian idea graph.

The maintenance flow has two jobs:

1. Turn graph health into a human review queue for weekly organizing.
2. Check recent application records for idea / bridge candidates without
   promoting those candidates into formal graph links.

It deliberately writes evidence receipts under outputs/ only. It does not edit
the vault graph, concept notes, source notes, application notes, or registries.
"""

from __future__ import annotations
import os

import argparse
import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    from tools.obsidian_idea_retrieval import (
        IDEA_TYPES,
        NOTE_LINK_RE,
        load_concept_notes,
        read_markdown,
        run_five_question_acceptance,
        unique_notes,
        unique_preserve,
    )
except ModuleNotFoundError:
    from obsidian_idea_retrieval import (  # type: ignore
        IDEA_TYPES,
        NOTE_LINK_RE,
        load_concept_notes,
        read_markdown,
        run_five_question_acceptance,
        unique_notes,
        unique_preserve,
    )


SCHEMA_VERSION = "obsidian_semantic_maintenance_v1"
DEFAULT_VAULT = Path(os.environ.get("CREATIVE_SECOND_BRAIN_VAULT", "knowledge-base-vault"))
DEFAULT_OUTPUT_DIR = Path("outputs/obsidian_semantic_maintenance")
TOOL_PATH = "tools/obsidian_semantic_maintenance.py"


@dataclass(frozen=True)
class ApplicationRecord:
    title: str
    path: Path
    rel_path: str
    fields: dict[str, Any]
    body: str


@dataclass(frozen=True)
class IdeaMatchRule:
    title: str
    idea_type: str
    relation: str
    terms: tuple[str, ...]


IDEA_MATCH_RULES: tuple[IdeaMatchRule, ...] = (
    IdeaMatchRule("AI 採用與組織變化", "hub", "連到 Hub", ("AI 採用", "組織", "部署", "團隊", "顧問", "轉型", "consult", "enterprise")),
    IdeaMatchRule("工具使用邊界", "hub", "連到 Hub", ("工具", "agent", "Agent", "MCP", "workflow", "流程", "委派", "失敗處理")),
    IdeaMatchRule("個人知識管理與 2nd Brain", "hub", "連到 Hub", ("知識庫", "知識管理", "SSOT", "2nd Brain", "Obsidian", "記憶", "保存")),
    IdeaMatchRule("品味與生活策展", "hub", "連到 Hub", ("品味", "生活", "策展", "候選", "選擇", "審美", "探索")),
    IdeaMatchRule("學習方法與記憶", "hub", "連到 Hub", ("學習", "記憶", "理解", "提取", "溫書", "創作")),
    IdeaMatchRule("效率 vs 品味", "tension", "形成取捨", ("效率", "速度", "商品化", "更快", "產出", "scale")),
    IdeaMatchRule("自動化 vs 人的判斷", "tension", "形成取捨", ("自動化", "AI", "Agent", "人的判斷", "Human", "問責", "judgment")),
    IdeaMatchRule("保存資料 vs 改變行動", "tension", "形成取捨", ("保存", "行動", "落地", "採納", "改變", "驗證", "SSOT")),
    IdeaMatchRule("工具越多 vs 系統越亂", "tension", "形成取捨", ("工具", "流程", "複雜", "失敗", "維護", "多工具")),
    IdeaMatchRule("快速答案 vs 深層理解", "tension", "形成取捨", ("答案", "理解", "慢讀", "追問", "學習", "記得")),
    IdeaMatchRule("偏好可驗證的完成證據", "taste_signal", "品味線索", ("可驗證", "證據", "讀回", "完成", "追溯", "驗收")),
    IdeaMatchRule("偏好低摩擦但可追溯的工具流程", "taste_signal", "品味線索", ("低摩擦", "可追溯", "自然留下", "融入日常", "工具流程")),
    IdeaMatchRule("抗拒只有噱頭的 AI 展示", "taste_signal", "品味線索", ("噱頭", "熱門", "新奇", "展示", "不能落地", "反平台")),
    IdeaMatchRule("如何選擇 AI 工具", "application_scene", "可應用於", ("選 AI 工具", "評估 AI", "採用 AI", "工具是否值得")),
    IdeaMatchRule("如何判斷一篇內容值得入庫", "application_scene", "可應用於", ("值得入庫", "入庫", "收藏", "採用判斷", "information gain")),
    IdeaMatchRule("如何建立個人品味候選池", "application_scene", "可應用於", ("候選池", "候選", "探索", "推薦", "品味候選")),
    IdeaMatchRule("如何設計 Obsidian 知識網", "application_scene", "可應用於", ("Obsidian", "graph", "知識網", "關聯圖", "語義圖")),
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build Obsidian idea graph maintenance receipts.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--weekly-organize", action="store_true", help="Create the weekly organizing queue.")
    group.add_argument("--new-content-candidates", action="store_true", help="Create recent-content idea candidate receipts.")
    group.add_argument("--acceptance", action="store_true", help="Run weekly organizing, new-content coupling, and five-question retrieval checks.")
    parser.add_argument("--vault", type=Path, default=DEFAULT_VAULT)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--recent-limit", type=int, default=5)
    parser.add_argument("--max-review-items", type=int, default=50)
    parser.add_argument("--source-id", action="append", default=[], help="Limit new-content candidate checks to a source id such as KS-VS-0036.")
    parser.add_argument("--write-receipts", action="store_true")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    root = Path.cwd().resolve()
    vault = resolve_inside(root, args.vault)
    output_dir = resolve_inside(root, args.output_dir)

    if args.weekly_organize:
        result = build_weekly_organizing_receipt(vault=vault, max_review_items=max(1, args.max_review_items))
        if args.write_receipts:
            result["receipt_path"] = write_receipt(output_dir, "weekly_organizing.json", result)
        print_result(result, args.json)
        return 1 if args.check and result["status"] != "ready" else 0

    if args.new_content_candidates:
        result = build_new_content_candidates_receipt(
            vault=vault,
            recent_limit=max(1, args.recent_limit),
            source_ids=[str(item) for item in args.source_id],
        )
        if args.write_receipts:
            result["receipt_path"] = write_receipt(output_dir, "new_content_candidates.json", result)
        print_result(result, args.json)
        return 1 if args.check and result["status"] != "ready" else 0

    result = run_acceptance(
        vault=vault,
        output_dir=output_dir,
        recent_limit=max(1, args.recent_limit),
        max_review_items=max(1, args.max_review_items),
        source_ids=[str(item) for item in args.source_id],
        write_receipts=args.write_receipts,
    )
    print_result(result, args.json)
    return 1 if args.check and result["status"] != "ready" else 0


def run_acceptance(
    *,
    vault: Path,
    output_dir: Path,
    recent_limit: int,
    max_review_items: int,
    source_ids: list[str],
    write_receipts: bool,
) -> dict[str, Any]:
    weekly = build_weekly_organizing_receipt(vault=vault, max_review_items=max_review_items)
    new_content = build_new_content_candidates_receipt(vault=vault, recent_limit=recent_limit, source_ids=source_ids)
    five_question = run_five_question_acceptance(vault=vault, output_dir=output_dir / "retrieval_check", write_receipts=False)
    status = "ready" if weekly["status"] == "ready" and new_content["status"] == "ready" and five_question["status"] == "ready" else "needs_review"
    result: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "receipt_type": "stage_4_5_completion",
        "status": status,
        "generated_at": now_text(),
        "vault": str(vault),
        "completion_claim": "Stage 4.5 creative 2nd Brain mechanism is ready when weekly organizing, new-content coupling, and five-question retrieval are all ready.",
        "truth_boundary": truth_boundary(),
        "weekly_organizing": compact_weekly_status(weekly),
        "new_content_coupling": compact_new_content_status(new_content),
        "five_question_retrieval": {
            "status": five_question["status"],
            "ready_count": five_question["ready_count"],
            "needs_bridge_count": five_question["needs_bridge_count"],
            "insufficient_evidence_count": five_question["insufficient_evidence_count"],
        },
        "remaining_living_backlog": weekly["organizing_queue"],
    }
    if write_receipts:
        result["weekly_receipt_path"] = write_receipt(output_dir, "weekly_organizing.json", weekly)
        result["new_content_receipt_path"] = write_receipt(output_dir, "new_content_candidates.json", new_content)
        result["summary_path"] = write_receipt(output_dir, "stage_4_5_completion.json", result)
    return result


def build_weekly_organizing_receipt(*, vault: Path, max_review_items: int) -> dict[str, Any]:
    notes = unique_notes(load_concept_notes(vault))
    adjacency = build_adjacency(notes)
    components = connected_components(adjacency)
    source_counts = component_source_counts(components, notes)
    component_rows = component_review_rows(components, notes, source_counts)
    orphans = sorted([node_key(note) for note in notes if not adjacency.get(node_key(note))], key=sort_text)
    orphan_rows = [orphan_review_row(title, notes) for title in orphans]
    small_component_rows = [
        row for row in component_rows if int(row["size"]) <= 6 and int(row["source_record_count"]) <= 1 and int(row["size"]) > 1
    ]
    organizing_queue = [*orphan_rows, *small_component_rows]
    reported_queue = organizing_queue[:max_review_items]
    edge_count = sum(len(targets) for targets in adjacency.values()) // 2
    status = "ready" if notes and all(row.get("recommended_action") for row in reported_queue) else "needs_review"
    return {
        "schema_version": SCHEMA_VERSION,
        "receipt_type": "weekly_organizing",
        "status": status,
        "generated_at": now_text(),
        "vault": str(vault),
        "summary": {
            "concept_or_idea_note_count": len(notes),
            "edge_count": edge_count,
            "linked_node_count": sum(1 for targets in adjacency.values() if targets),
            "component_count": len(components),
            "largest_component_size": max((len(component) for component in components), default=0),
            "multi_source_component_count": sum(1 for row in component_rows if int(row["source_record_count"]) >= 2),
            "orphan_count": len(orphans),
            "source_local_small_component_count": len(small_component_rows),
        },
        "organizing_rule": "每週先處理 orphan、source-local 小群與缺 idea role 的 component；人工覆核後才升格為正式 bridge。",
        "orphan_actions": orphan_rows,
        "small_component_actions": small_component_rows,
        "organizing_queue": reported_queue,
        "unreported_queue_count": max(0, len(organizing_queue) - len(reported_queue)),
        "truth_boundary": truth_boundary(),
    }


def build_new_content_candidates_receipt(*, vault: Path, recent_limit: int, source_ids: list[str], application_paths: list[Path] | None = None) -> dict[str, Any]:
    notes = unique_notes(load_concept_notes(vault))
    applications = load_application_records(vault, paths=application_paths)
    selected = select_application_records(applications, recent_limit=recent_limit, source_ids=source_ids)
    rows = [new_content_candidate_row(app, notes) for app in selected]
    ready_statuses = {"candidate_ready", "no_reusable_idea_declared"}
    status = "ready" if rows and all(row["candidate_status"] in ready_statuses for row in rows) else "needs_review"
    return {
        "schema_version": SCHEMA_VERSION,
        "receipt_type": "new_content_candidates",
        "status": status,
        "generated_at": now_text(),
        "vault": str(vault),
        "selection": {
            "source_ids": source_ids,
            "recent_limit": recent_limit,
            "reviewed_count": len(rows),
        },
        "candidate_rule": "新增內容只保存 idea / bridge 候選或明確 no reusable idea；正式 wikilink 要等週期整理或人工覆核。",
        "records": rows,
        "truth_boundary": truth_boundary(),
    }


def new_content_candidate_row(app: ApplicationRecord, notes: list[Any]) -> dict[str, Any]:
    fields = app.fields
    source_id = str(fields.get("source_id") or "").strip()
    source_note = str(fields.get("source_note") or "").strip()
    application_id = str(fields.get("application_id") or app.path.stem.split("_", 1)[0]).strip()
    text = searchable_application_text(app)
    concept_candidates = concept_candidates_for_record(notes, source_id=source_id, source_note=source_note, application_stem=app.path.stem)
    idea_matches = idea_matches_for_text(text, notes)
    bridge_candidates = build_bridge_candidates(app, idea_matches)
    explicit_no_reusable = has_explicit_no_reusable_signal(fields)
    if concept_candidates or bridge_candidates:
        candidate_status = "candidate_ready"
    elif explicit_no_reusable:
        candidate_status = "no_reusable_idea_declared"
    else:
        candidate_status = "needs_human_review"
    return {
        "application_id": application_id,
        "source_id": source_id,
        "source_note": source_note,
        "title": app.title,
        "application_path": app.rel_path,
        "source_path": str(fields.get("source_entry") or ""),
        "candidate_status": candidate_status,
        "formal_graph_write_allowed": False,
        "human_review_required": True,
        "concept_candidates": concept_candidates[:8],
        "bridge_candidates": bridge_candidates[:12],
        "explicit_no_reusable_idea": explicit_no_reusable,
        "next_step": "週期整理時覆核候選；有證據才補 hub、tension、taste_signal、application_scene 或 typed bridge。",
    }


def concept_candidates_for_record(notes: list[Any], *, source_id: str, source_note: str, application_stem: str) -> list[dict[str, str]]:
    keys = {source_id, source_note, application_stem}
    if source_id:
        keys.add(f"APP-{source_id}")
    candidates: list[dict[str, str]] = []
    for note in notes:
        if getattr(note, "idea_type", "") in IDEA_TYPES:
            continue
        records = {
            *getattr(note, "source_records", []),
            *getattr(note, "application_records", []),
            *getattr(note, "knowledge_point_records", []),
        }
        if not records.intersection(keys):
            continue
        candidates.append(
            {
                "title": note.title,
                "path": note.rel_path,
                "reason": "此 concept 已由同一 source / application record 產生，可作新增內容接合候選。",
            }
        )
    return sorted(candidates, key=lambda row: sort_text(row["title"]))


def idea_matches_for_text(text: str, notes: list[Any]) -> list[dict[str, str]]:
    existing = {note.title: note for note in notes}
    matches: list[dict[str, str]] = []
    normalized = normalize(text)
    for rule in IDEA_MATCH_RULES:
        note = existing.get(rule.title)
        if not note:
            continue
        matched_terms = [term for term in rule.terms if normalize(term) in normalized]
        if not matched_terms:
            continue
        matches.append(
            {
                "title": rule.title,
                "type": rule.idea_type,
                "path": note.rel_path,
                "relation": rule.relation,
                "matched_terms": "、".join(unique_preserve(matched_terms[:5])),
            }
        )
    return matches


def build_bridge_candidates(app: ApplicationRecord, idea_matches: list[dict[str, str]]) -> list[dict[str, str]]:
    evidence = first_non_empty(
        field_values(app.fields.get("compounding_insight_links")),
        field_values(app.fields.get("taste_signals")),
        [str(app.fields.get("compounding_next_action") or "").strip()],
        [str(app.fields.get("adoption_reason") or "").strip()],
        [str(app.fields.get("taste_network_reason") or "").strip()],
    )
    evidence_text = evidence[:180] if evidence else "由 application note 欄位與內容文字命中；仍需人工覆核。"
    rows: list[dict[str, str]] = []
    for match in idea_matches:
        rows.append(
            {
                "target": match["title"],
                "target_type": match["type"],
                "relation": match["relation"],
                "evidence": evidence_text,
                "status": "candidate_only",
            }
        )
    return rows


def has_explicit_no_reusable_signal(fields: dict[str, Any]) -> bool:
    compounding = str(fields.get("compounding_insight_status") or "").lower()
    adoption = str(fields.get("adoption_status") or fields.get("AI總判斷") or "").lower()
    taste = str(fields.get("taste_network_status") or fields.get("taste_link_status") or "").lower()
    return (
        compounding in {"no_insight", "needs_context", "none", "not_applicable", "暫無", "不適用"}
        and taste in {"不入網", "not_applicable", "none", "不適用", ""}
        and adoption in {"略過", "不採用", "skip", "reject", ""}
    )


def load_application_records(vault: Path, *, paths: list[Path] | None = None) -> list[ApplicationRecord]:
    app_dir = vault / "applications"
    if not app_dir.exists():
        return []
    records: list[ApplicationRecord] = []
    for path in sorted(paths if paths is not None else app_dir.rglob("*.md")):
        if not path.resolve().is_relative_to(app_dir.resolve()):
            raise ValueError("application path is outside the selected Vault applications")
        fields, body = read_markdown(path)
        title = str(fields.get("title") or path.stem).strip()
        records.append(ApplicationRecord(title=title, path=path, rel_path=path.relative_to(vault).as_posix(), fields=fields, body=body))
    return records


def select_application_records(applications: list[ApplicationRecord], *, recent_limit: int, source_ids: list[str]) -> list[ApplicationRecord]:
    if source_ids:
        wanted = set(source_ids)
        return [app for app in applications if str(app.fields.get("source_id") or "") in wanted]
    return sorted(applications, key=lambda app: (record_date(app), app.rel_path), reverse=True)[:recent_limit]


def record_date(app: ApplicationRecord) -> str:
    return str(app.fields.get("updated") or app.fields.get("created") or "")


def searchable_application_text(app: ApplicationRecord) -> str:
    parts = [
        app.title,
        str(app.fields.get("source_id") or ""),
        str(app.fields.get("source_note") or ""),
        str(app.fields.get("adoption_reason") or ""),
        str(app.fields.get("taste_network_reason") or ""),
        str(app.fields.get("compounding_next_action") or ""),
        str(app.fields.get("exploration_intent") or ""),
        app.body[:4000],
    ]
    for key in (
        "personalization_links",
        "taste_links",
        "taste_signals",
        "taste_exclusion_signals",
        "life_scenes",
        "compounding_insight_links",
        "compounding_insight_types",
    ):
        parts.extend(field_values(app.fields.get(key)))
    return "\n".join(part for part in parts if part)


def build_adjacency(notes: list[Any]) -> dict[str, set[str]]:
    titles = {node_key(note) for note in notes}
    adjacency: dict[str, set[str]] = {node_key(note): set() for note in notes}
    for note in notes:
        for target in NOTE_LINK_RE.findall(note.body):
            current = node_key(note)
            if target not in titles or target == current:
                continue
            adjacency[current].add(target)
            adjacency[target].add(current)
    return adjacency


def connected_components(adjacency: dict[str, set[str]]) -> list[set[str]]:
    components: list[set[str]] = []
    seen: set[str] = set()
    for node in sorted(adjacency, key=sort_text):
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
            stack.extend(sorted(adjacency[current] - seen, key=sort_text))
        components.append(component)
    return sorted(components, key=lambda component: (-len(component), sort_text(sorted(component, key=sort_text)[0])))


def component_source_counts(components: list[set[str]], notes: list[Any]) -> dict[str, int]:
    by_title = {node_key(note): note for note in notes}
    counts: dict[str, int] = {}
    for component in components:
        sources: set[str] = set()
        for title in component:
            sources.update(getattr(by_title[title], "source_records", []))
        counts[component_key(component)] = len(sources)
    return counts


def component_review_rows(components: list[set[str]], notes: list[Any], source_counts: dict[str, int]) -> list[dict[str, Any]]:
    by_title = {node_key(note): note for note in notes}
    rows: list[dict[str, Any]] = []
    for component in components:
        types = sorted({idea_type_for_note(by_title[title]) for title in component})
        missing_roles = [role for role in ("hub", "tension", "taste_signal", "application_scene") if role not in types]
        source_count = source_counts[component_key(component)]
        sample = sorted(component, key=sort_text)[:8]
        rows.append(
            {
                "kind": "source_local_small_component" if len(component) > 1 else "single_node_component",
                "size": len(component),
                "source_record_count": source_count,
                "idea_types_present": types,
                "missing_idea_roles": missing_roles,
                "sample": sample,
                "recommended_action": recommended_component_action(len(component), source_count, missing_roles, sample),
                "formal_graph_write_allowed": False,
            }
        )
    return rows


def orphan_review_row(title: str, notes: list[Any]) -> dict[str, Any]:
    note = next(note for note in notes if node_key(note) == title)
    return {
        "kind": "orphan",
        "title": title,
        "path": note.rel_path,
        "idea_type": idea_type_for_note(note),
        "source_records": getattr(note, "source_records", [])[:5],
        "recommended_action": "每週覆核：保留為獨立概念、合併到近似 concept、連到既有 hub / tension / taste signal，或退役為 record-only。",
        "formal_graph_write_allowed": False,
    }


def recommended_component_action(size: int, source_count: int, missing_roles: list[str], sample: list[str]) -> str:
    if size == 1:
        return "單點先不硬連；等有第二份來源或應用場景後再決定。"
    if source_count <= 1:
        return "這是來源內小團；先問是否可接到既有 hub / tension / application_scene，否則維持 record-local。"
    if "hub" in missing_roles:
        return "已有跨來源訊號但缺 hub；優先補主題入口。"
    return "已有連接；只在能說明支持、反例、取捨或應用時才補 typed bridge。"


def compact_weekly_status(receipt: dict[str, Any]) -> dict[str, Any]:
    summary = receipt["summary"]
    return {
        "status": receipt["status"],
        "orphan_count": summary["orphan_count"],
        "source_local_small_component_count": summary["source_local_small_component_count"],
        "organizing_queue_count": len(receipt["organizing_queue"]),
        "unreported_queue_count": receipt["unreported_queue_count"],
    }


def compact_new_content_status(receipt: dict[str, Any]) -> dict[str, Any]:
    rows = receipt["records"]
    return {
        "status": receipt["status"],
        "reviewed_count": len(rows),
        "candidate_ready_count": sum(1 for row in rows if row["candidate_status"] == "candidate_ready"),
        "no_reusable_idea_count": sum(1 for row in rows if row["candidate_status"] == "no_reusable_idea_declared"),
        "needs_human_review_count": sum(1 for row in rows if row["candidate_status"] == "needs_human_review"),
    }


def field_values(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def first_non_empty(*groups: list[str]) -> str:
    for group in groups:
        for value in group:
            if value.strip():
                return value.strip()
    return ""


def idea_type_for_note(note: Any) -> str:
    value = str(getattr(note, "idea_type", "") or "")
    return value if value in IDEA_TYPES else "concept"


def component_key(component: set[str]) -> str:
    return "\n".join(sorted(component, key=sort_text))


def node_key(note: Any) -> str:
    return Path(getattr(note, "path")).stem


def normalize(text: str) -> str:
    return re.sub(r"\s+", "", text.casefold())


def sort_text(value: str) -> str:
    return value.casefold()


def write_receipt(output_dir: Path, filename: str, payload: dict[str, Any]) -> str:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / filename
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    return path.as_posix()


def resolve_inside(root: Path, path: Path) -> Path:
    candidate = path if path.is_absolute() else root / path
    resolved = candidate.resolve()
    if resolved != root and root not in resolved.parents:
        raise SystemExit(f"path outside workspace: {path}")
    return resolved


def truth_boundary() -> str:
    return (
        "Receipts are generated from Obsidian vault notes and registries only. "
        "They are review evidence, not graph nodes, and they do not promote candidates into formal links."
    )


def now_text() -> str:
    return datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %z")


def print_result(result: dict[str, Any], json_mode: bool) -> None:
    if json_mode:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    print(f"status: {result['status']}")
    print(f"receipt_type: {result.get('receipt_type', '')}")
    if result.get("receipt_type") == "stage_4_5_completion":
        print(f"weekly: {result['weekly_organizing']['status']}")
        print(f"new_content: {result['new_content_coupling']['status']}")
        print(f"five_question: {result['five_question_retrieval']['status']}")


if __name__ == "__main__":
    raise SystemExit(main())
