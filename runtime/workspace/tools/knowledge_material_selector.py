from __future__ import annotations

import argparse
import json
import os
import re
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    from tools.outcome_page_generator import DEFAULT_VAULT, list_value, read_markdown_record, single_match
    from tools.research_catalog import (
        derived_application_records,
        list_research_topics,
        match_terms,
        query_terms,
        topic_profile_matches,
        topic_profile_text,
        topic_source_bindings,
    )
    from tools.validation_manifest import build_embedded_manifest
except ModuleNotFoundError:
    from outcome_page_generator import DEFAULT_VAULT, list_value, read_markdown_record, single_match  # type: ignore
    from research_catalog import (  # type: ignore
        derived_application_records,
        list_research_topics,
        match_terms,
        query_terms,
        topic_profile_matches,
        topic_profile_text,
        topic_source_bindings,
    )
    from validation_manifest import build_embedded_manifest  # type: ignore


SELECTOR_SCHEMA_VERSION = "0.1"
DEFAULT_LIMIT = 5


FIELD_WEIGHTS = {
    "title": 7,
    "summary": 6,
    "knowledge_use": 4,
    "knowledge_point": 8,
    "application": 5,
    "reader_layer": 6,
    "relation": 4,
    "source_body": 1,
    "research_topic": 40,
}


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Select answer materials from the Obsidian vault without relying on model memory."
    )
    parser.add_argument("--query", required=True, help="Question or topic to select materials for.")
    parser.add_argument("--vault", type=Path, default=DEFAULT_VAULT)
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    parser.add_argument("--source-id", action="append", help="Optional source id filter. Can be used more than once.")
    parser.add_argument("--topic-id", action="append", help="Optional research topic filter. Can be used more than once.")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    if args.limit < 1:
        parser.error("--limit must be greater than 0")

    result = select_materials(
        query=args.query,
        vault=args.vault,
        limit=args.limit,
        source_ids=args.source_id,
        topic_ids=args.topic_id,
    )
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"status: {result['status']}")
        print(f"query: {result['query']}")
        print(f"candidates: {result['candidate_count']}")
        for candidate in result["candidates"]:
            print(f"- {candidate['source_id']} score={candidate['score']} {candidate['title']}")
            for path in candidate["must_read"]:
                print(f"  read: {path}")
    return 0 if result["status"] == "ready" else 1


def select_materials(
    *,
    query: str,
    vault: Path = DEFAULT_VAULT,
    limit: int = DEFAULT_LIMIT,
    source_ids: list[str] | None = None,
    topic_ids: list[str] | None = None,
) -> dict[str, Any]:
    vault = vault.resolve()
    terms = query_terms(query)
    blocked_reasons: list[dict[str, str]] = []

    if not terms:
        return build_result(
            vault=vault,
            query=query,
            terms=[],
            candidates=[],
            blocked_reasons=[{"reason": "empty_query", "message": "查詢沒有可比對字詞。"}],
        )
    if limit < 1:
        raise ValueError("limit must be greater than 0")

    source_rows = read_jsonl(vault / "_registry" / "sources.jsonl", blocked_reasons)
    kp_rows = read_jsonl(vault / "_registry" / "knowledge_points.jsonl", blocked_reasons)
    relation_rows = read_jsonl(vault / "_registry" / "relations.jsonl", blocked_reasons)

    source_filter = set(source_ids or [])
    research_bindings = topic_source_bindings(Path(os.environ.get("CREATIVE_SECOND_BRAIN_ROOT", str(vault.parent))).resolve(), topic_ids)
    topic_query_matches = research_topic_query_matches(Path(os.environ.get("CREATIVE_SECOND_BRAIN_ROOT", str(vault.parent))).resolve(), terms)
    if topic_ids:
        source_filter.update(research_bindings)
    if source_filter:
        source_rows = [row for row in source_rows if str(row.get("source_id", "")) in source_filter]

    kp_by_source: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in kp_rows:
        kp_by_source[str(row.get("source_id", ""))].append(row)

    relations_by_source: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in relation_rows:
        relations_by_source[str(row.get("source_id", ""))].append(row)

    candidates: list[dict[str, Any]] = []
    for row in source_rows:
        source_id = str(row.get("source_id", "")).strip()
        if not source_id:
            continue
        candidate = build_candidate(
            vault=vault,
            source_id=source_id,
            source_registry=row,
            kp_registry_rows=kp_by_source.get(source_id, []),
            relation_rows=relations_by_source.get(source_id, []),
            terms=terms,
            research_binding=research_bindings.get(source_id, {}),
            topic_query_matches=topic_query_matches,
        )
        if candidate["score"] > 0:
            candidates.append(candidate)

    candidates.sort(key=lambda item: (item["score"], item["source_id"]), reverse=True)
    candidates = candidates[:limit]
    return build_result(vault=vault, query=query, terms=terms, candidates=candidates, blocked_reasons=blocked_reasons)


def build_candidate(
    *,
    vault: Path,
    source_id: str,
    source_registry: dict[str, Any],
    kp_registry_rows: list[dict[str, Any]],
    relation_rows: list[dict[str, Any]],
    terms: list[str],
    research_binding: dict[str, Any] | None = None,
    topic_query_matches: dict[str, dict[str, object]] | None = None,
) -> dict[str, Any]:
    missing: list[str] = []
    evidence: list[dict[str, Any]] = []
    score = 0
    matched_terms: Counter[str] = Counter()

    source_note = resolve_source_note(vault, source_id, source_registry, missing)
    app_note = resolve_app_note(vault, source_id, missing, source_note)
    source_fields, source_body = read_record_or_empty(source_note, missing, "source_note")
    app_fields, app_body = read_record_or_empty(app_note, missing, "application_note")
    derived_records = derived_application_records(vault, source_id)

    title = text_field(source_fields, "title") or source_id
    summary = text_field(source_fields, "summary")
    source_url = text_field(source_fields, "source_url") or str(source_registry.get("source_url", ""))

    for topic_id in sorted(set((research_binding or {}).get("topic_ids", []))):
        topic_match = (topic_query_matches or {}).get(topic_id)
        if not topic_match:
            continue
        score += add_evidence(
            evidence,
            matched_terms,
            sorted(set(topic_match["matches"])),
            FIELD_WEIGHTS["research_topic"],
            "research_topic_profile",
            str(topic_match["text"]),
            None,
            vault,
            title=str(topic_match["title"]),
        )

    score += add_evidence(
        evidence,
        matched_terms,
        terms,
        FIELD_WEIGHTS["title"],
        "source_title",
        title,
        source_note,
        vault,
    )
    derived_paths: list[str] = []
    related_applications: list[dict[str, Any]] = []
    for record in derived_records:
        path = vault / str(record.get("application_note_path") or "")
        fields, body = read_record_or_empty(path, missing, "derived_application_note")
        derived_paths.append(vault_relative(path, vault) if path.exists() else "")
        material = application_material_text(fields, body)
        score += add_evidence(
            evidence,
            matched_terms,
            terms,
            FIELD_WEIGHTS["application"],
            "derived_application_note",
            material,
            path if path.exists() else None,
            vault,
            title=str(record.get("title") or ""),
        )
        related_applications.append({
            key: value for key, value in record.items() if key != "body"
        })
    score += add_evidence(
        evidence,
        matched_terms,
        terms,
        FIELD_WEIGHTS["summary"],
        "source_summary",
        summary,
        source_note,
        vault,
    )
    score += add_evidence(
        evidence,
        matched_terms,
        terms,
        FIELD_WEIGHTS["knowledge_use"],
        "knowledge_use",
        "、".join(list_value(source_fields.get("knowledge_use"))),
        source_note,
        vault,
    )
    score += add_evidence(
        evidence,
        matched_terms,
        terms,
        FIELD_WEIGHTS["source_body"],
        "source_body",
        excerpt_text(source_body, 6000),
        source_note,
        vault,
    )

    kp_evidence, kp_score = score_knowledge_points(
        vault=vault,
        source_id=source_id,
        kp_registry_rows=kp_registry_rows,
        terms=terms,
        matched_terms=matched_terms,
        missing=missing,
    )
    evidence.extend(kp_evidence)
    score += kp_score

    app_text = application_material_text(app_fields, app_body)
    score += add_evidence(
        evidence,
        matched_terms,
        terms,
        FIELD_WEIGHTS["application"],
        "application_note",
        app_text,
        app_note,
        vault,
    )
    reader_text = reader_layer_text(app_body)
    score += add_evidence(
        evidence,
        matched_terms,
        terms,
        FIELD_WEIGHTS["reader_layer"],
        "reader_layer",
        reader_text,
        app_note,
        vault,
    )

    linked_relation_rows = [row for row in relation_rows if str(row.get("status", "linked")) == "linked"]
    relation_text = "\n".join(
        " ".join(str(row.get(key, "")) for key in ("relation", "evidence", "source_kp_id", "target_kp_id"))
        for row in linked_relation_rows
    )
    relation_matches = match_terms(relation_text, terms) if relation_text else set()
    score += add_evidence(
        evidence,
        matched_terms,
        terms,
        FIELD_WEIGHTS["relation"],
        "relations",
        relation_text,
        vault / "_registry" / "relations.jsonl",
        vault,
    )

    must_read = []
    if source_note:
        must_read.append(vault_relative(source_note, vault))
    must_read.extend(sorted({str(item["path"]) for item in evidence if item.get("kind") == "knowledge_point"}))
    if app_note:
        must_read.append(vault_relative(app_note, vault))
    must_read.extend(path for path in derived_paths if path)
    must_read.extend(relation_endpoint_paths(vault, kp_registry_rows, linked_relation_rows, relation_matches))
    must_read = list(dict.fromkeys(path for path in must_read if path))

    answer_material_status = "ready" if not missing and must_read else "needs_review"
    return {
        "source_id": source_id,
        "score": score,
        "answer_material_status": answer_material_status,
        "missing_materials": missing,
        "title": title,
        "summary": summary,
        "source_url": source_url,
        "source_note": vault_relative(source_note, vault) if source_note else "",
        "application_note": vault_relative(app_note, vault) if app_note else "",
        "research_topic_ids": sorted(set((research_binding or {}).get("topic_ids", []))),
        "classifications": list((research_binding or {}).get("classifications", [])),
        "related_applications": related_applications,
        "knowledge_point_count": len([item for item in evidence if item.get("kind") == "knowledge_point"]),
        "matched_terms": sorted(matched_terms),
        "evidence_snippets": trim_evidence(evidence),
        "must_read": must_read,
        "answer_rule": "先讀 must_read 內的 source note、application note、已列衍生應用與 knowledge-point note；再作答。不可只憑模型記憶。",
    }


def score_knowledge_points(
    *,
    vault: Path,
    source_id: str,
    kp_registry_rows: list[dict[str, Any]],
    terms: list[str],
    matched_terms: Counter[str],
    missing: list[str],
) -> tuple[list[dict[str, Any]], int]:
    evidence: list[dict[str, Any]] = []
    score = 0
    kp_dir = vault / "knowledge-points" / "by-source" / source_id
    kp_paths = [vault / str(row.get("kp_note_path", "")) for row in kp_registry_rows if row.get("kp_note_path")]
    if not kp_paths and kp_dir.exists():
        kp_paths = sorted(kp_dir.glob("*.md"))
    if not kp_paths:
        missing.append("knowledge_points")
        return evidence, score

    for path in sorted(set(kp_paths)):
        if not path_is_inside_vault(path, vault):
            missing.append("knowledge_point_outside_vault")
            continue
        fields, body = read_record_or_empty(path, missing, "knowledge_point")
        text = " ".join(
            [
                text_field(fields, "kp_id"),
                text_field(fields, "knowledge_point"),
                text_field(fields, "definition"),
                text_field(fields, "knowledge_type"),
                text_field(fields, "source_anchor"),
                "、".join(list_value(fields.get("applicable_scenes"))),
                "、".join(list_value(fields.get("usable_for"))),
                excerpt_text(body, 1200),
            ]
        )
        score += add_evidence(
            evidence,
            matched_terms,
            terms,
            FIELD_WEIGHTS["knowledge_point"],
            "knowledge_point",
            text,
            path,
            vault,
            title=text_field(fields, "knowledge_point") or text_field(fields, "definition") or path.stem,
        )
    return evidence, score


def relation_endpoint_paths(
    vault: Path,
    kp_registry_rows: list[dict[str, Any]],
    relation_rows: list[dict[str, Any]],
    relation_matches: set[str],
) -> list[str]:
    if not relation_matches:
        return []
    by_kp_id = {
        str(row.get("kp_id", "")).strip(): str(row.get("kp_note_path", "")).strip()
        for row in kp_registry_rows
        if row.get("kp_id") and row.get("kp_note_path")
    }
    paths: list[str] = []
    for row in relation_rows:
        row_text = " ".join(str(row.get(key, "")) for key in ("relation", "evidence", "source_kp_id", "target_kp_id"))
        if not match_terms(row_text, list(relation_matches)):
            continue
        for key in ("source_kp_id", "target_kp_id"):
            kp_path = by_kp_id.get(str(row.get(key, "")).strip())
            if kp_path and path_is_inside_vault(vault / kp_path, vault):
                paths.append(vault_relative(vault / kp_path, vault))
    return sorted(dict.fromkeys(paths))


def add_evidence(
    evidence: list[dict[str, Any]],
    matched_terms: Counter[str],
    terms: list[str],
    weight: int,
    kind: str,
    text: str,
    path: Path | None,
    vault: Path,
    *,
    title: str = "",
) -> int:
    if not text:
        return 0
    matches = match_terms(text, terms)
    if not matches:
        return 0
    for term in matches:
        matched_terms[term] += 1
    evidence.append(
        {
            "kind": kind,
            "path": vault_relative(path, vault) if path else "",
            "title": title,
            "matched_terms": sorted(matches),
            "snippet": make_snippet(text, matches),
        }
    )
    return len(matches) * weight


def build_result(
    *,
    vault: Path,
    query: str,
    terms: list[str],
    candidates: list[dict[str, Any]],
    blocked_reasons: list[dict[str, str]],
) -> dict[str, Any]:
    status = "ready"
    if blocked_reasons or not candidates:
        status = "needs_review"
    if any(candidate["answer_material_status"] != "ready" for candidate in candidates):
        status = "needs_review"
    return {
        "status": status,
        "mode": "dry_run",
        "schema_version": SELECTOR_SCHEMA_VERSION,
        "generated_at": datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %z"),
        "query": query,
        "query_terms": terms,
        "vault": str(vault),
        "truth_source": "Obsidian vault registry, source notes, application notes, knowledge-point notes, and relation registry",
        "truth_boundary": "This tool selects answer materials only. It does not answer the question and does not replace reading the selected notes.",
        "validation_manifest": selector_manifest(),
        "candidate_count": len(candidates),
        "candidates": candidates,
        "blocked_reasons": blocked_reasons,
        "fallback_rule": "如無候選或 status 非 ready，回答前必須標明未能從本地知識庫選出足夠素材，不能靠 AI 記憶補作已入庫證據。",
    }


def selector_manifest() -> dict[str, Any]:
    try:
        return build_embedded_manifest("knowledge-retrieval", root=Path(__file__).resolve().parents[1])
    except Exception as exc:  # pragma: no cover
        return {
            "status": "needs_review",
            "scenario": "knowledge-retrieval",
            "blocked_reasons": [{"reason": "manifest_build_failed", "message": str(exc)}],
        }


def read_jsonl(path: Path, blocked_reasons: list[dict[str, str]]) -> list[dict[str, Any]]:
    if not path.exists():
        blocked_reasons.append({"reason": "missing_registry", "path": str(path)})
        return []
    rows: list[dict[str, Any]] = []
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            blocked_reasons.append({"reason": "invalid_jsonl", "path": str(path), "line": str(line_number), "message": str(exc)})
            continue
        if isinstance(value, dict):
            rows.append(value)
    return rows


def resolve_source_note(vault: Path, source_id: str, row: dict[str, Any], missing: list[str]) -> Path | None:
    registry_path = str(row.get("source_note_path", "")).strip()
    if registry_path:
        path = vault / registry_path
        if not path_is_inside_vault(path, vault):
            missing.append("source_note_outside_vault")
            return None
        if path.exists():
            return path
        missing.append("source_note")
        return None
    try:
        return single_match(vault / "sources", f"{source_id}_*.md", "source note")
    except SystemExit:
        missing.append("source_note")
        return None


def resolve_app_note(vault: Path, source_id: str, missing: list[str], source_note: Path | None = None) -> Path | None:
    try:
        if source_note is not None:
            try:
                from tools.outcome_page_generator import resolve_application_note
            except ModuleNotFoundError:
                from outcome_page_generator import resolve_application_note
            path = resolve_application_note(vault, source_id, source_note)
        else:
            path = single_match(vault / "applications", f"APP-{source_id}_*.md", "application note")
        if not path_is_inside_vault(path, vault):
            missing.append("application_note_outside_vault")
            return None
        return path
    except SystemExit:
        missing.append("application_note")
        return None


def path_is_inside_vault(path: Path, vault: Path) -> bool:
    return path.resolve().is_relative_to(vault.resolve()) and path.resolve() != vault.resolve()


def read_record_or_empty(path: Path | None, missing: list[str], label: str) -> tuple[dict[str, object], str]:
    if not path or not path.exists():
        if label not in missing:
            missing.append(label)
        return {}, ""
    fields, body = read_markdown_record(path)
    return fields, clean_body_for_selection(body)


def clean_body_for_selection(body: str) -> str:
    lines = []
    skip_vault_links = False
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("> Vault note generated from"):
            continue
        if stripped == "## Vault Links":
            skip_vault_links = True
            continue
        if skip_vault_links and stripped.startswith("## "):
            skip_vault_links = False
        if skip_vault_links:
            continue
        lines.append(line)
    return "\n".join(lines).strip()


def application_material_text(fields: dict[str, object], body: str) -> str:
    field_parts = []
    for key in (
        "adoption_reason",
        "ai_adoption_reason",
        "personal_relevance",
        "taste_network_reason",
        "compounding_next_action",
        "personalization_links",
        "taste_links",
        "compounding_insight_links",
        "compounding_insight_types",
        "life_scenes",
    ):
        value = fields.get(key)
        if isinstance(value, list):
            field_parts.append(" ".join(str(item) for item in value))
        elif value:
            field_parts.append(str(value))
    return "\n".join(field_parts + [excerpt_text(body, 5000)])


def reader_layer_text(body: str) -> str:
    match = re.search(r"(?ms)^## 讀取層成果頁內容\s*(.*?)(?=^## |\Z)", body)
    return match.group(1).strip() if match else ""


def research_topic_query_matches(root: Path, terms: list[str]) -> dict[str, dict[str, object]]:
    """Promote only cases whose declared topic intent matches a natural request."""
    matches_by_topic: dict[str, dict[str, object]] = {}
    for topic in list_research_topics(root).get("topics", []):
        matches = topic_profile_matches(topic, terms)
        if not matches:
            continue
        topic_id = str(topic.get("topic_id", "")).strip()
        if topic_id:
            matches_by_topic[topic_id] = {
                "matches": matches,
                "text": topic_profile_text(topic),
                "title": str(topic.get("display_name") or topic_id),
            }
    return matches_by_topic


def make_snippet(text: str, matches: set[str], limit: int = 160) -> str:
    compact = re.sub(r"\s+", " ", text).strip()
    if not compact:
        return ""
    first_index = min((compact.lower().find(term.lower()) for term in matches if compact.lower().find(term.lower()) >= 0), default=0)
    start = max(0, first_index - 45)
    snippet = compact[start : start + limit].strip()
    if start > 0:
        snippet = "..." + snippet
    if start + limit < len(compact):
        snippet += "..."
    return snippet


def trim_evidence(evidence: list[dict[str, Any]], limit: int = 8) -> list[dict[str, Any]]:
    return evidence[:limit]


def excerpt_text(text: str, limit: int) -> str:
    text = text.strip()
    return text if len(text) <= limit else text[:limit]


def text_field(fields: dict[str, object], key: str) -> str:
    value = fields.get(key)
    if isinstance(value, list):
        return "、".join(str(item) for item in value if str(item).strip())
    return str(value or "").strip()


def vault_relative(path: Path | None, vault: Path) -> str:
    if not path:
        return ""
    try:
        return path.resolve().relative_to(vault.resolve()).as_posix()
    except ValueError:
        return str(path.resolve())


if __name__ == "__main__":
    raise SystemExit(main())
