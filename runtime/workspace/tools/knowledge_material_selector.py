from __future__ import annotations

import argparse
import json
import math
import os
import re
from collections import Counter, defaultdict
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

try:
    from tools.obsidian_vault_adapter import retained_original_location
    from tools.outcome_page_generator import (
        DEFAULT_VAULT,
        completed_v2_package_for_standard_outcome,
        list_value,
        read_markdown_record,
        single_match,
    )
    from tools.research_catalog import (
        derived_application_records,
        list_research_topics,
        query_terms,
        topic_profile_matches,
        topic_profile_text,
        topic_source_bindings,
    )
    from tools.validation_manifest import build_embedded_manifest
except ModuleNotFoundError:
    from obsidian_vault_adapter import retained_original_location  # type: ignore
    from outcome_page_generator import (  # type: ignore
        DEFAULT_VAULT,
        completed_v2_package_for_standard_outcome,
        list_value,
        read_markdown_record,
        single_match,
    )
    from research_catalog import (  # type: ignore
        derived_application_records,
        list_research_topics,
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
    "canonical_source": 1.5,
    "research_topic": 40,
}

# Keep selector-specific query handling here.  research_catalog's helpers also
# drive topic discovery and must retain their existing matching semantics.
ENGLISH_STOPWORDS = frozenset({
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "in", "is",
    "it", "of", "on", "or", "that", "the", "this", "to", "what", "when", "where",
    "which", "who", "why", "with", "how",
})
CHINESE_STOPWORDS = frozenset({
    "的", "了", "是", "在", "有", "和", "與", "及", "或", "而", "也", "都", "將", "得", "更",
    "這", "那", "一個", "甚麼", "什么", "如何", "怎樣", "怎样", "為何", "为什么", "哪些",
    "是否", "可以", "應該", "嗎", "呢", "吧",
})
CHINESE_NGRAM_BOUNDARY_STOPWORDS = frozenset({
    "甚麼", "什么", "如何", "怎樣", "怎样", "為何", "为什么", "哪些", "是否", "可以", "應該",
    "得更", "嗎", "呢", "吧",
})
CHINESE_STOPWORD_PATTERN = re.compile("|".join(
    re.escape(term) for term in sorted(CHINESE_NGRAM_BOUNDARY_STOPWORDS, key=lambda term: (-len(term), term))
))
EXPANSION_WEIGHT = 0.8
TITLE_PHRASE_BOOST = 28.0
SOURCE_TITLE_PHRASE_SCALE = 3.0
EXACT_TITLE_MATCH_BOOST = 140.0
KP_TITLE_PHRASE_SCALE = 0.25
CANONICAL_PHRASE_SCALE = 1.0
KP_SCORE_FACTORS = (1.0, 0.35, 0.15)


class SelectionTextCache:
    """Call-local normalized text, token, and query-term lookup cache."""

    def __init__(self) -> None:
        self._normalized: dict[str, str] = {}
        self._english_words: dict[str, frozenset[str]] = {}
        self._word_variants: dict[str, frozenset[str]] = {}
        self._text_english_variants: dict[str, frozenset[str]] = {}
        self._tokens: dict[str, tuple[str, ...]] = {}
        self._length_normalizations: dict[str, float] = {}
        self._term_variants: dict[tuple[str, ...], dict[str, frozenset[str]]] = {}
        self._phrase_indexes: dict[tuple[tuple[tuple[str, ...], float], ...], dict[int, dict[tuple[str, ...], float]]] = {}

    def normalized(self, text: str) -> str:
        if text not in self._normalized:
            self._normalized[text] = text.casefold()
        return self._normalized[text]

    def english_words(self, text: str) -> frozenset[str]:
        if text not in self._english_words:
            self._english_words[text] = frozenset(re.findall(r"[A-Za-z0-9_]+", self.normalized(text)))
        return self._english_words[text]

    def tokens(self, text: str) -> tuple[str, ...]:
        if text not in self._tokens:
            self._tokens[text] = tuple(selection_tokens(text))
        return self._tokens[text]

    def word_variants(self, word: str) -> frozenset[str]:
        if word not in self._word_variants:
            self._word_variants[word] = english_variants(word)
        return self._word_variants[word]

    def english_variants_for_text(self, text: str) -> frozenset[str]:
        if text not in self._text_english_variants:
            variants: set[str] = set()
            for word in self.english_words(text):
                variants.update(self.word_variants(word))
            self._text_english_variants[text] = frozenset(variants)
        return self._text_english_variants[text]

    def length_normalization(self, text: str) -> float:
        if text not in self._length_normalizations:
            token_count = len(re.findall(r"[A-Za-z0-9_]+", text)) + sum(
                "\u4e00" <= char <= "\u9fff" for char in text
            )
            token_count = max(1, token_count)
            self._length_normalizations[text] = max(
                0.25, 1.0 / (1.0 + 0.75 * max(0.0, token_count / 160.0 - 1.0))
            )
        return self._length_normalizations[text]

    def term_variants(self, terms: list[str]) -> dict[str, frozenset[str]]:
        key = tuple(terms)
        if key not in self._term_variants:
            variants: dict[str, set[str]] = defaultdict(set)
            for term in terms:
                if term.isascii():
                    for variant in english_variants(term):
                        variants[variant].add(term)
            self._term_variants[key] = {
                variant: frozenset(items) for variant, items in variants.items()
            }
        return self._term_variants[key]

    def phrase_index(
        self, phrases: list[tuple[tuple[str, ...], float]]
    ) -> dict[int, dict[tuple[str, ...], float]]:
        key = tuple(phrases)
        if key not in self._phrase_indexes:
            self._phrase_indexes[key] = phrase_index(phrases)
        return self._phrase_indexes[key]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Select answer materials from the Obsidian vault without relying on model memory."
    )
    parser.add_argument("--query", required=True, help="Question or topic to select materials for.")
    parser.add_argument("--vault", type=Path, default=DEFAULT_VAULT)
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    parser.add_argument("--source-id", action="append", help="Optional source id filter. Can be used more than once.")
    parser.add_argument("--topic-id", action="append", help="Optional research topic filter. Can be used more than once.")
    parser.add_argument("--expand", action="append", default=[], help="Alternate wording of the same question; repeatable.")
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
        expansions=args.expand,
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
            for path in candidate.get("knowledge_point_paths", []):
                print(f"  knowledge point: {path}")
    return 0 if result["status"] == "ready" else 1


def select_materials(
    *,
    query: str,
    vault: Path = DEFAULT_VAULT,
    limit: int = DEFAULT_LIMIT,
    source_ids: list[str] | None = None,
    topic_ids: list[str] | None = None,
    expansions: list[str] | None = None,
) -> dict[str, Any]:
    """Select materials for one query without retaining resolver state for later calls."""
    return _select_materials(
        query=query,
        vault=vault,
        limit=limit,
        source_ids=source_ids,
        topic_ids=topic_ids,
        expansions=expansions,
    )


def select_materials_many(
    *,
    queries: list[str],
    vault: Path = DEFAULT_VAULT,
    limit: int = DEFAULT_LIMIT,
) -> list[dict[str, Any]]:
    """Select many independent queries while sharing one call-local vault index."""
    vault = vault.resolve()
    index: SelectionIndex | None = None
    results: list[dict[str, Any]] = []
    for query in queries:
        terms, _, _, _ = selection_query(query, [])
        if terms and index is None:
            index = SelectionIndex(vault)
        results.append(_select_materials(query=query, vault=vault, limit=limit, index=index))
    return results


class SelectionIndex:
    """Read registry state and package resolution once for one batch call only."""

    def __init__(self, vault: Path):
        self.vault = vault
        self.blocked_reasons: list[dict[str, str]] = []
        self.source_rows = read_jsonl(vault / "_registry" / "sources.jsonl", self.blocked_reasons)
        kp_rows = read_jsonl(vault / "_registry" / "knowledge_points.jsonl", self.blocked_reasons)
        relation_rows = read_jsonl(vault / "_registry" / "relations.jsonl", self.blocked_reasons)
        self.kp_by_source: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in kp_rows:
            self.kp_by_source[str(row.get("source_id", ""))].append(row)
        self.relations_by_source: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in relation_rows:
            self.relations_by_source[str(row.get("source_id", ""))].append(row)
        self.root = Path(os.environ.get("CREATIVE_SECOND_BRAIN_ROOT", str(vault.parent))).resolve()
        self.package_resolver = PackageMatchResolver(vault)
        self._derived_records: dict[str, list[dict[str, Any]]] = {}
        self._source_materials: dict[str, dict[str, Any]] = {}
        self._knowledge_point_records: dict[str, tuple[bool, list[tuple[Path, dict[str, object], str]], list[str]]] = {}
        self._matching_texts: dict[str, str] = {}
        self._canonical_texts: dict[str, str] = {}
        self.resolved_paths: dict[Path, Path] = {}
        self.text_cache = SelectionTextCache()

    def derived_records_for(self, source_id: str) -> list[dict[str, Any]]:
        if source_id not in self._derived_records:
            self._derived_records[source_id] = derived_application_records(self.vault, source_id)
        return self._derived_records[source_id]

    def source_material_for(self, source_id: str, source_registry: dict[str, Any]) -> dict[str, Any]:
        if source_id not in self._source_materials:
            missing: list[str] = []
            source_note = resolve_source_note(self.vault, source_id, source_registry, missing)
            app_note = resolve_app_note(self.vault, source_id, missing, source_note)
            source_fields, source_body = read_record_or_empty(source_note, missing, "source_note")
            app_fields, app_body = read_record_or_empty(app_note, missing, "application_note")
            self._source_materials[source_id] = {
                "missing": missing,
                "source_note": source_note,
                "app_note": app_note,
                "source_fields": source_fields,
                "source_body": source_body,
                "app_fields": app_fields,
                "app_body": app_body,
            }
        return self._source_materials[source_id]

    def knowledge_points_for(self, source_id: str) -> tuple[bool, list[tuple[Path, dict[str, object], str]], list[str]]:
        if source_id not in self._knowledge_point_records:
            kp_dir = self.vault / "knowledge-points" / "by-source" / source_id
            kp_paths = [self.vault / str(row.get("kp_note_path") or "") for row in self.kp_by_source.get(source_id, []) if row.get("kp_note_path")]
            if not kp_paths and kp_dir.exists():
                kp_paths = sorted(kp_dir.glob("*.md"))
            records: list[tuple[Path, dict[str, object], str]] = []
            missing: list[str] = []
            for path in sorted(set(kp_paths)):
                if not path_is_inside_vault(path, self.vault):
                    missing.append("knowledge_point_outside_vault")
                    continue
                fields, body = read_record_or_empty(path, missing, "knowledge_point")
                records.append((path, fields, body))
            self._knowledge_point_records[source_id] = (bool(kp_paths), records, missing)
        return self._knowledge_point_records[source_id]

    def matching_text_for(self, source_id: str, source_registry: dict[str, Any]) -> str:
        if source_id not in self._matching_texts:
            source_material = self.source_material_for(source_id, source_registry)
            self._matching_texts[source_id] = source_matching_text(
                self.vault,
                source_id,
                source_registry,
                self.kp_by_source.get(source_id, []),
                self.package_resolver,
                source_material=source_material,
                prepared_knowledge_points=self.knowledge_points_for(source_id),
                canonical_text=self.canonical_text_for(source_id, source_registry, source_material["source_fields"]),
            )
        return self._matching_texts[source_id]

    def canonical_text_for(
        self, source_id: str, source_registry: dict[str, Any], source_fields: dict[str, object]
    ) -> str:
        if source_id not in self._canonical_texts:
            self._canonical_texts[source_id] = canonical_source_text_for_matching(
                self.vault, source_id, source_registry, source_fields, self.package_resolver
            )
        return self._canonical_texts[source_id]


def _select_materials(
    *,
    query: str,
    vault: Path,
    limit: int,
    source_ids: list[str] | None = None,
    topic_ids: list[str] | None = None,
    expansions: list[str] | None = None,
    index: SelectionIndex | None = None,
) -> dict[str, Any]:
    vault = vault.resolve()
    clean_expansions = [item.strip() for item in (expansions or []) if item and item.strip()]
    terms, term_multipliers, phrases, exact_title_phrases = selection_query(query, clean_expansions)

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

    index = index or SelectionIndex(vault)
    blocked_reasons = list(index.blocked_reasons)
    source_rows = index.source_rows

    source_filter = set(source_ids or [])
    research_bindings = topic_source_bindings(index.root, topic_ids)
    # Topic matching stays on research_catalog's current helper contract.
    topic_query_matches = research_topic_query_matches(index.root, query_terms(query))
    if topic_ids:
        source_filter.update(research_bindings)
    if source_filter:
        source_rows = [row for row in source_rows if str(row.get("source_id", "")) in source_filter]

    term_weights = term_idf_weights(
        vault=vault,
        source_rows=source_rows,
        kp_by_source=index.kp_by_source,
        terms=terms,
        term_multipliers=term_multipliers,
        package_resolver=index.package_resolver,
        text_cache=index.text_cache,
        matching_texts={
            str(row.get("source_id") or "").strip(): index.matching_text_for(str(row.get("source_id") or "").strip(), row)
            for row in source_rows if str(row.get("source_id") or "").strip()
        },
    )

    candidates: list[dict[str, Any]] = []
    for row in source_rows:
        source_id = str(row.get("source_id", "")).strip()
        if not source_id:
            continue
        candidate = build_candidate(
            vault=vault,
            source_id=source_id,
            source_registry=row,
            kp_registry_rows=index.kp_by_source.get(source_id, []),
            relation_rows=index.relations_by_source.get(source_id, []),
            derived_records=index.derived_records_for(source_id),
            source_material=index.source_material_for(source_id, row),
            prepared_knowledge_points=index.knowledge_points_for(source_id),
            terms=terms,
            term_weights=term_weights,
            phrases=phrases,
            exact_title_phrases=exact_title_phrases,
            research_binding=research_bindings.get(source_id, {}),
            topic_query_matches=topic_query_matches,
            package_resolver=index.package_resolver,
            text_cache=index.text_cache,
            resolved_paths=index.resolved_paths,
            canonical_text=index.canonical_text_for(
                source_id, row, index.source_material_for(source_id, row)["source_fields"]
            ),
        )
        if candidate["score"] > 0:
            candidates.append(candidate)

    candidates.sort(key=lambda item: (item["score"], item["source_id"]), reverse=True)
    candidates = candidates[:limit]
    return build_result(
        vault=vault,
        query=query,
        terms=terms,
        candidates=candidates,
        blocked_reasons=blocked_reasons,
    )


def build_candidate(
    *,
    vault: Path,
    source_id: str,
    source_registry: dict[str, Any],
    kp_registry_rows: list[dict[str, Any]],
    relation_rows: list[dict[str, Any]],
    derived_records: list[dict[str, Any]] | None = None,
    source_material: dict[str, Any] | None = None,
    prepared_knowledge_points: tuple[bool, list[tuple[Path, dict[str, object], str]], list[str]] | None = None,
    terms: list[str],
    term_weights: dict[str, float],
    phrases: list[tuple[tuple[str, ...], float]],
    exact_title_phrases: list[tuple[tuple[str, ...], float]],
    research_binding: dict[str, Any] | None = None,
    topic_query_matches: dict[str, dict[str, object]] | None = None,
    package_resolver: PackageMatchResolver | None = None,
    text_cache: SelectionTextCache | None = None,
    resolved_paths: dict[Path, Path] | None = None,
    canonical_text: str | None = None,
) -> dict[str, Any]:
    missing: list[str] = list(source_material["missing"]) if source_material is not None else []
    evidence: list[dict[str, Any]] = []
    score = 0.0
    matched_terms: Counter[str] = Counter()

    if source_material is None:
        source_note = resolve_source_note(vault, source_id, source_registry, missing)
        app_note = resolve_app_note(vault, source_id, missing, source_note)
        source_fields, source_body = read_record_or_empty(source_note, missing, "source_note")
        app_fields, app_body = read_record_or_empty(app_note, missing, "application_note")
    else:
        source_note = source_material["source_note"]
        app_note = source_material["app_note"]
        source_fields = source_material["source_fields"]
        source_body = source_material["source_body"]
        app_fields = source_material["app_fields"]
        app_body = source_material["app_body"]
    derived_records = derived_records if derived_records is not None else derived_application_records(vault, source_id)

    title = text_field(source_fields, "title") or source_id
    summary = text_field(source_fields, "summary")
    source_url = text_field(source_fields, "source_url") or str(source_registry.get("source_url", ""))
    source_label = text_field(source_fields, "source_label") or text_field(source_fields, "來源標示")
    retrieval_aliases = list_value(source_fields.get("retrieval_aliases"))
    source_title_parts = (title, source_label, *retrieval_aliases)

    for topic_id in sorted(set((research_binding or {}).get("topic_ids", []))):
        topic_match = (topic_query_matches or {}).get(topic_id)
        if not topic_match:
            continue
        topic_terms = sorted(selection_match_terms(str(topic_match["text"]), terms, text_cache))
        if not topic_terms:
            continue
        score += add_evidence(
            evidence,
            matched_terms,
            topic_terms,
            FIELD_WEIGHTS["research_topic"],
            "research_topic_profile",
            str(topic_match["text"]),
            None,
            vault,
            title=str(topic_match["title"]),
            term_weights=term_weights,
            phrases=[],
            text_cache=text_cache,
            resolved_paths=resolved_paths,
        )

    score += add_evidence(
        evidence,
        matched_terms,
        terms,
        FIELD_WEIGHTS["title"],
        "source_title",
        "\n".join(item for item in source_title_parts if item),
        source_note,
        vault,
        term_weights=term_weights,
        phrases=phrases,
        phrase_text="\n".join(item for item in source_title_parts if item),
        phrase_scale=SOURCE_TITLE_PHRASE_SCALE,
        text_cache=text_cache,
    )
    score += exact_title_phrase_boost(source_title_parts, exact_title_phrases, text_cache)
    derived_paths: list[str] = []
    related_applications: list[dict[str, Any]] = []
    for record in derived_records:
        path = vault / str(record.get("application_note_path") or "")
        fields, body = read_record_or_empty(path, missing, "derived_application_note")
        derived_paths.append(vault_relative(path, vault, resolved_paths) if path.exists() else "")
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
            term_weights=term_weights,
            phrases=phrases,
            text_cache=text_cache,
            resolved_paths=resolved_paths,
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
        term_weights=term_weights,
        phrases=phrases,
        text_cache=text_cache,
        resolved_paths=resolved_paths,
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
        term_weights=term_weights,
        phrases=phrases,
        text_cache=text_cache,
        resolved_paths=resolved_paths,
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
        term_weights=term_weights,
        phrases=phrases,
        text_cache=text_cache,
        resolved_paths=resolved_paths,
    )

    score += score_canonical_source_matching(
        vault=vault,
        source_id=source_id,
        source_registry=source_registry,
        source_fields=source_fields,
        terms=terms,
        term_weights=term_weights,
        phrases=phrases,
        matched_terms=matched_terms,
        package_resolver=package_resolver,
        text_cache=text_cache,
        canonical_text=canonical_text,
    )

    kp_evidence, kp_score = score_knowledge_points(
        vault=vault,
        source_id=source_id,
        kp_registry_rows=kp_registry_rows,
        terms=terms,
        term_weights=term_weights,
        phrases=phrases,
        matched_terms=matched_terms,
        missing=missing,
        prepared_knowledge_points=prepared_knowledge_points,
        text_cache=text_cache,
        resolved_paths=resolved_paths,
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
        term_weights=term_weights,
        phrases=phrases,
        text_cache=text_cache,
        resolved_paths=resolved_paths,
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
        term_weights=term_weights,
        phrases=phrases,
        text_cache=text_cache,
        resolved_paths=resolved_paths,
    )

    linked_relation_rows = [row for row in relation_rows if str(row.get("status", "linked")) == "linked"]
    relation_text = "\n".join(
        " ".join(str(row.get(key, "")) for key in ("relation", "evidence", "source_kp_id", "target_kp_id"))
        for row in linked_relation_rows
    )
    relation_matches = selection_match_terms(relation_text, terms, text_cache) if relation_text else set()
    score += add_evidence(
        evidence,
        matched_terms,
        terms,
        FIELD_WEIGHTS["relation"],
        "relations",
        relation_text,
        vault / "_registry" / "relations.jsonl",
        vault,
        term_weights=term_weights,
        phrases=phrases,
        text_cache=text_cache,
        resolved_paths=resolved_paths,
    )

    must_read = []
    if source_note:
        must_read.append(vault_relative(source_note, vault, resolved_paths))
    if app_note:
        must_read.append(vault_relative(app_note, vault, resolved_paths))
    must_read.extend(path for path in derived_paths if path)
    must_read = list(dict.fromkeys(path for path in must_read if path))

    matched_knowledge_point_paths = [
        str(item.get("path", ""))
        for item in sorted(
            (item for item in evidence if item.get("kind") == "knowledge_point"),
            key=lambda item: (-len(item.get("matched_terms", [])), str(item.get("path", ""))),
        )
    ]
    knowledge_point_paths = list(dict.fromkeys([
        *matched_knowledge_point_paths,
        *relation_endpoint_paths(vault, kp_registry_rows, linked_relation_rows, relation_matches, text_cache, resolved_paths),
    ]))
    knowledge_point_paths = [path for path in knowledge_point_paths if path and path not in must_read]

    answer_material_status = "ready" if not missing and must_read else "needs_review"
    return {
        "source_id": source_id,
        "score": round(score, 3),
        "answer_material_status": answer_material_status,
        "missing_materials": missing,
        "title": title,
        "summary": summary,
        "source_url": source_url,
        "source_note": vault_relative(source_note, vault, resolved_paths) if source_note else "",
        "application_note": vault_relative(app_note, vault, resolved_paths) if app_note else "",
        "research_topic_ids": sorted(set((research_binding or {}).get("topic_ids", []))),
        "classifications": list((research_binding or {}).get("classifications", [])),
        "related_applications": related_applications,
        "knowledge_point_count": len([item for item in evidence if item.get("kind") == "knowledge_point"]),
        "matched_terms": sorted(matched_terms),
        "evidence_snippets": trim_evidence(evidence),
        "must_read": must_read,
        "knowledge_point_paths": knowledge_point_paths,
        "answer_rule": "先讀 must_read 內的 source note（已載知識點草案）、application note 與已列衍生應用；knowledge_point_paths 是命中問題的知識點筆記，按命中排序，在讀取上限內有餘位才載入。不可只憑模型記憶。",
    }


def score_knowledge_points(
    *,
    vault: Path,
    source_id: str,
    kp_registry_rows: list[dict[str, Any]],
    terms: list[str],
    matched_terms: Counter[str],
    missing: list[str],
    term_weights: dict[str, float] | None = None,
    phrases: list[tuple[tuple[str, ...], float]] | None = None,
    prepared_knowledge_points: tuple[bool, list[tuple[Path, dict[str, object], str]], list[str]] | None = None,
    text_cache: SelectionTextCache | None = None,
    resolved_paths: dict[Path, Path] | None = None,
) -> tuple[list[dict[str, Any]], float]:
    evidence: list[dict[str, Any]] = []
    per_note_scores: list[float] = []
    term_weights = term_weights or {term: 1.0 for term in terms}
    phrases = phrases or []
    if prepared_knowledge_points is None:
        kp_dir = vault / "knowledge-points" / "by-source" / source_id
        kp_paths = [vault / str(row.get("kp_note_path", "")) for row in kp_registry_rows if row.get("kp_note_path")]
        if not kp_paths and kp_dir.exists():
            kp_paths = sorted(kp_dir.glob("*.md"))
        records: list[tuple[Path, dict[str, object], str]] = []
        for path in sorted(set(kp_paths)):
            if not path_is_inside_vault(path, vault):
                missing.append("knowledge_point_outside_vault")
                continue
            fields, body = read_record_or_empty(path, missing, "knowledge_point")
            records.append((path, fields, body))
    else:
        has_paths, records, cached_missing = prepared_knowledge_points
        kp_paths = [Path()] if has_paths else []
        missing.extend(cached_missing)
    if not kp_paths:
        missing.append("knowledge_points")
        return evidence, 0.0

    for path, fields, body in records:
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
        per_note_scores.append(add_evidence(
            evidence,
            matched_terms,
            terms,
            FIELD_WEIGHTS["knowledge_point"],
            "knowledge_point",
            text,
            path,
            vault,
            title=text_field(fields, "knowledge_point") or text_field(fields, "definition") or path.stem,
            term_weights=term_weights,
            phrases=phrases,
            phrase_text=text_field(fields, "knowledge_point") or text_field(fields, "definition") or path.stem,
            phrase_scale=KP_TITLE_PHRASE_SCALE,
            text_cache=text_cache,
            resolved_paths=resolved_paths,
        ))
    ranked = sorted(per_note_scores, reverse=True)
    # A source may have many valid knowledge points, but repeated notes are not
    # independent evidence of relevance.  Retain only the best three with a
    # fixed diminishing contribution.
    score = sum(value * factor for value, factor in zip(ranked, KP_SCORE_FACTORS))
    return evidence, score


def relation_endpoint_paths(
    vault: Path,
    kp_registry_rows: list[dict[str, Any]],
    relation_rows: list[dict[str, Any]],
    relation_matches: set[str],
    text_cache: SelectionTextCache | None = None,
    resolved_paths: dict[Path, Path] | None = None,
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
        if not selection_match_terms(row_text, list(relation_matches), text_cache):
            continue
        for key in ("source_kp_id", "target_kp_id"):
            kp_path = by_kp_id.get(str(row.get(key, "")).strip())
            if kp_path and path_is_inside_vault(vault / kp_path, vault):
                paths.append(vault_relative(vault / kp_path, vault, resolved_paths))
    return sorted(dict.fromkeys(paths))


def add_evidence(
    evidence: list[dict[str, Any]],
    matched_terms: Counter[str],
    terms: list[str],
    weight: float,
    kind: str,
    text: str,
    path: Path | None,
    vault: Path,
    *,
    title: str = "",
    term_weights: dict[str, float],
    phrases: list[tuple[tuple[str, ...], float]],
    phrase_text: str = "",
    phrase_scale: float = 1.0,
    text_cache: SelectionTextCache | None = None,
    resolved_paths: dict[Path, Path] | None = None,
) -> float:
    if not text:
        return 0.0
    matches = selection_match_terms(text, terms, text_cache)
    if not matches:
        return 0.0
    for term in matches:
        matched_terms[term] += 1
    evidence.append(
        {
            "kind": kind,
            "path": vault_relative(path, vault, resolved_paths) if path else "",
            "title": title,
            "matched_terms": sorted(matches),
            "snippet": make_snippet(text, matches),
        }
    )
    return weighted_field_score(
        text=text,
        matches=matches,
        term_weights=term_weights,
        weight=weight,
        phrases=phrases,
        phrase_text=phrase_text,
        phrase_scale=phrase_scale,
        text_cache=text_cache,
    )


def selection_query(
    query: str, expansions: list[str]
) -> tuple[list[str], dict[str, float], list[tuple[tuple[str, ...], float]], list[tuple[tuple[str, ...], float]]]:
    """Return selector-local terms and phrase sequences, preserving alternate wording provenance."""
    multipliers: dict[str, float] = {}
    phrases: list[tuple[tuple[str, ...], float]] = []
    exact_title_phrases: list[tuple[tuple[str, ...], float]] = []
    for text, multiplier in [(query, 1.0), *((item, EXPANSION_WEIGHT) for item in expansions)]:
        tokens = selection_tokens(text)
        for token in tokens:
            multipliers[token] = max(multipliers.get(token, 0.0), multiplier)
        if tokens:
            # A source title can itself be a one- or two-word name (for
            # example, "Agents").  The complete query is still an exact
            # phrase and must not be diluted by its inflected common term.
            phrases.append((tuple(tokens), multiplier))
            exact_title_phrases.append((tuple(tokens), multiplier))
        # Full wording and long contiguous portions distinguish a named source
        # from a page that only repeats generic individual words.
        for width in range(3, len(tokens) + 1):
            for start in range(0, len(tokens) - width + 1):
                phrases.append((tuple(tokens[start : start + width]), multiplier))
    unique_phrases = list(dict.fromkeys(phrases))
    return sorted(multipliers), multipliers, unique_phrases, list(dict.fromkeys(exact_title_phrases))


def selection_tokens(text: str) -> list[str]:
    terms: list[str] = []
    for chunk in re.findall(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]+", text.casefold()):
        if chunk.isascii():
            if chunk not in ENGLISH_STOPWORDS:
                terms.append(chunk)
            continue
        # Split only complete question/function phrases before deriving
        # n-grams.  Never split at individual function characters: 得／更 in
        # 獲得、更新、更換 are part of content words, while 得更 is a complete
        # question boundary in 「怎樣學習得更有效？」.
        for segment in filter(None, CHINESE_STOPWORD_PATTERN.split(chunk)):
            candidates = [segment] if len(segment) <= 2 else [segment[index : index + 2] for index in range(len(segment) - 1)]
            terms.extend(item for item in candidates if item not in CHINESE_STOPWORDS)
    return terms


@lru_cache(maxsize=4096)
def english_variants(term: str) -> frozenset[str]:
    """Small dependency-free English normalizer for retrieval, not a stemmer for research topics."""
    word = term.casefold()
    variants = {word}
    if len(word) > 4 and word.endswith("ies"):
        variants.add(word[:-3] + "y")
    if len(word) > 3 and word.endswith("es"):
        variants.add(word[:-2])
    if len(word) > 3 and word.endswith("s"):
        variants.add(word[:-1])
    if len(word) > 5 and word.endswith("ing"):
        stem = word[:-3]
        variants.add(stem)
        variants.add(stem + "e")
    if len(word) > 4 and word.endswith("ed"):
        stem = word[:-2]
        variants.add(stem)
        variants.add(stem + "e")
    return frozenset(variants)


def terms_equivalent(left: str, right: str) -> bool:
    if left == right:
        return True
    if not left.isascii() or not right.isascii():
        return False
    return bool(english_variants(left).intersection(english_variants(right)))


def selection_match_terms(
    text: str,
    terms: list[str],
    text_cache: SelectionTextCache | None = None,
) -> set[str]:
    normalized = text_cache.normalized(text) if text_cache else text.casefold()
    matches: set[str] = set()
    if text_cache is not None:
        text_variants = text_cache.english_variants_for_text(text)
        for term in terms:
            if term.isascii():
                if english_variants(term).intersection(text_variants):
                    matches.add(term)
            elif term in normalized:
                matches.add(term)
        return matches
    english_variants_to_terms = text_cache.term_variants(terms) if text_cache else defaultdict(set)
    for term in terms:
        if term.isascii():
            for variant in english_variants(term):
                english_variants_to_terms[variant].add(term)
    for term in terms:
        if not term.isascii():
            if term in normalized:
                matches.add(term)
            continue
    # This is the same symmetric variant intersection as terms_equivalent(),
    # indexed once per text instead of recomputing every term/word pair.
    words = text_cache.english_words(text) if text_cache else set(re.findall(r"[A-Za-z0-9_]+", normalized))
    for word in words:
        variants = text_cache.word_variants(word) if text_cache else english_variants(word)
        for variant in variants:
            matches.update(english_variants_to_terms.get(variant, set()))
    return matches


def phrase_index(
    phrases: list[tuple[tuple[str, ...], float]]
) -> dict[int, dict[tuple[str, ...], float]]:
    indexed: dict[int, dict[tuple[str, ...], float]] = defaultdict(dict)
    for phrase, multiplier in phrases:
        values = indexed[len(phrase)]
        values[phrase] = max(values.get(phrase, 0.0), multiplier)
    return indexed


def phrase_match_weight(
    text: str,
    phrases: list[tuple[tuple[str, ...], float]],
    text_cache: SelectionTextCache | None = None,
) -> float:
    if not phrases:
        return 0.0
    tokens = text_cache.tokens(text) if text_cache else selection_tokens(text)
    best = 0.0
    by_width = text_cache.phrase_index(phrases) if text_cache else phrase_index(phrases)
    for width, phrases_at_width in by_width.items():
        if width > len(tokens):
            continue
        for start in range(len(tokens) - width + 1):
            multiplier = phrases_at_width.get(tuple(tokens[start : start + width]))
            if multiplier is not None:
                best = max(best, width * multiplier)
    return best


def exact_title_phrase_boost(
    title_parts: tuple[str, ...],
    phrases: list[tuple[tuple[str, ...], float]],
    text_cache: SelectionTextCache | None = None,
) -> float:
    """Reward a source's complete name, not a longer title that merely contains it."""
    best = 0.0
    for title in title_parts:
        title_tokens = text_cache.tokens(title) if text_cache else tuple(selection_tokens(title))
        for phrase, multiplier in phrases:
            if phrase == title_tokens:
                best = max(best, EXACT_TITLE_MATCH_BOOST * multiplier)
    return best


def length_normalization(text: str, text_cache: SelectionTextCache | None = None) -> float:
    if text_cache is not None:
        return text_cache.length_normalization(text)
    token_count = len(re.findall(r"[A-Za-z0-9_]+", text)) + sum("\u4e00" <= char <= "\u9fff" for char in text)
    token_count = max(1, token_count)
    # BM25-style saturation prevents a long archive or body from gaining a
    # linear advantage merely because it contains more words.
    return max(0.25, 1.0 / (1.0 + 0.75 * max(0.0, token_count / 160.0 - 1.0)))


def weighted_field_score(
    *,
    text: str,
    matches: set[str],
    term_weights: dict[str, float],
    weight: float,
    phrases: list[tuple[tuple[str, ...], float]],
    phrase_text: str = "",
    phrase_scale: float = 1.0,
    text_cache: SelectionTextCache | None = None,
) -> float:
    term_score = sum(term_weights.get(term, 0.0) for term in matches)
    phrase_score = phrase_match_weight(phrase_text, phrases, text_cache) * TITLE_PHRASE_BOOST * phrase_scale
    return weight * length_normalization(text, text_cache) * term_score + phrase_score


def term_idf_weights(
    *,
    vault: Path,
    source_rows: list[dict[str, Any]],
    kp_by_source: dict[str, list[dict[str, Any]]],
    terms: list[str],
    term_multipliers: dict[str, float],
    package_resolver: PackageMatchResolver | None = None,
    matching_texts: dict[str, str] | None = None,
    text_cache: SelectionTextCache | None = None,
) -> dict[str, float]:
    if not source_rows:
        return {term: 0.0 for term in terms}
    document_frequency: Counter[str] = Counter()
    for source_row in source_rows:
        source_id = str(source_row.get("source_id") or "").strip()
        if not source_id:
            continue
        text = (matching_texts or {}).get(source_id)
        if text is None:
            text = source_matching_text(vault, source_id, source_row, kp_by_source.get(source_id, []), package_resolver)
        present = selection_match_terms(text, terms, text_cache)
        document_frequency.update(present)
    total = len(source_rows)
    return {
        term: math.log(
            1.0 + (total - document_frequency[term] + 0.5) / (document_frequency[term] + 0.5)
        ) * term_multipliers.get(term, 1.0)
        for term in terms
    }


def source_matching_text(
    vault: Path,
    source_id: str,
    source_registry: dict[str, Any],
    kp_registry_rows: list[dict[str, Any]],
    package_resolver: PackageMatchResolver | None = None,
    *,
    source_material: dict[str, Any] | None = None,
    prepared_knowledge_points: tuple[bool, list[tuple[Path, dict[str, object], str]], list[str]] | None = None,
    canonical_text: str | None = None,
) -> str:
    if source_material is None:
        missing: list[str] = []
        source_note = resolve_source_note(vault, source_id, source_registry, missing)
        source_fields, source_body = read_record_or_empty(source_note, missing, "source_note")
    else:
        source_fields = source_material["source_fields"]
        source_body = source_material["source_body"]
    pieces = [
        text_field(source_fields, "title"),
        text_field(source_fields, "source_label"),
        text_field(source_fields, "來源標示"),
        *list_value(source_fields.get("retrieval_aliases")),
        text_field(source_fields, "summary"),
        "、".join(list_value(source_fields.get("knowledge_use"))),
        source_body,
    ]
    if prepared_knowledge_points is None:
        records = []
        for row in kp_registry_rows:
            path = vault / str(row.get("kp_note_path") or "")
            if not path_is_inside_vault(path, vault):
                continue
            fields, body = read_record_or_empty(path, [], "knowledge_point")
            records.append((path, fields, body))
    else:
        _, records, _ = prepared_knowledge_points
    for _, fields, body in records:
        pieces.extend((text_field(fields, "knowledge_point"), text_field(fields, "definition"), body))
    pieces.append(
        canonical_text if canonical_text is not None
        else canonical_source_text_for_matching(vault, source_id, source_registry, source_fields, package_resolver)
    )
    return "\n".join(piece for piece in pieces if piece)


class PackageMatchResolver:
    """Memoise formal package resolution for one selection call only."""

    def __init__(self, vault: Path):
        self.vault = vault
        self._packages: dict[str, Path | None] = {}

    def package_for(self, source_id: str) -> Path | None:
        if source_id not in self._packages:
            self._packages[source_id] = completed_package_for_matching(self.vault, source_id)
        return self._packages[source_id]


def completed_package_for_matching(
    vault: Path,
    source_id: str,
) -> Path | None:
    """Resolve through the existing receipt resolver without cross-call caching."""
    try:
        package_file = completed_v2_package_for_standard_outcome(
            vault,
            source_id,
            (vault / "outcome-pages" / f"{source_id}_outcome.html").resolve(),
        )
    except (OSError, ValueError, SystemExit):
        return None
    return package_file.parent.resolve() if package_file else None


def source_retention_mode(source_registry: dict[str, Any], source_fields: dict[str, object]) -> str:
    return str(source_registry.get("source_retention_mode") or source_fields.get("source_retention_mode") or "").strip()


def canonical_source_text_for_matching(
    vault: Path,
    source_id: str,
    source_registry: dict[str, Any],
    source_fields: dict[str, object],
    package_resolver: PackageMatchResolver | None = None,
) -> str:
    package_dir = (package_resolver or PackageMatchResolver(vault)).package_for(source_id)
    if not package_dir:
        return ""
    try:
        mode = source_retention_mode(source_registry, source_fields)
        bundle_path = package_dir / "input_bundle.json"
        if bundle_path.is_file():
            bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
            mode = bundle.get("source_retention_mode") or mode
        if retained_original_location(mode or None) != "package_canonical":
            return ""
        canonical_path = (package_dir / "canonical_source.md").resolve()
        if not canonical_path.is_relative_to(package_dir):
            return ""
        state = canonical_path.stat()
        return cached_canonical_source(str(canonical_path), state.st_mtime_ns, state.st_size)
    except (OSError, RuntimeError, UnicodeError, ValueError, json.JSONDecodeError):
        # Original archives are matching-only and optional: an unavailable
        # archive must not turn material selection into a failed request.
        return ""


@lru_cache(maxsize=512)
def cached_canonical_source(path_text: str, modified_ns: int, size: int) -> str:
    return Path(path_text).read_text(encoding="utf-8")


def score_canonical_source_matching(
    *,
    vault: Path,
    source_id: str,
    source_registry: dict[str, Any],
    source_fields: dict[str, object],
    terms: list[str],
    term_weights: dict[str, float],
    phrases: list[tuple[tuple[str, ...], float]],
    matched_terms: Counter[str],
    package_resolver: PackageMatchResolver | None = None,
    text_cache: SelectionTextCache | None = None,
    canonical_text: str | None = None,
) -> float:
    text = canonical_text if canonical_text is not None else canonical_source_text_for_matching(
        vault, source_id, source_registry, source_fields, package_resolver
    )
    matches = selection_match_terms(text, terms, text_cache)
    for term in matches:
        matched_terms[term] += 1
    return weighted_field_score(
        text=text,
        matches=matches,
        term_weights=term_weights,
        weight=FIELD_WEIGHTS["canonical_source"],
        phrases=phrases,
        phrase_text=text,
        phrase_scale=CANONICAL_PHRASE_SCALE,
        text_cache=text_cache,
    ) if matches else 0.0


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
    resolved_vault = vault.resolve()
    resolved_path = path.resolve()
    return resolved_path.is_relative_to(resolved_vault) and resolved_path != resolved_vault


def read_record_or_empty(path: Path | None, missing: list[str], label: str) -> tuple[dict[str, object], str]:
    if not path or not path.exists():
        if label not in missing:
            missing.append(label)
        return {}, ""
    try:
        state = path.stat()
    except OSError:
        if label not in missing:
            missing.append(label)
        return {}, ""
    return cached_selection_record(str(path.resolve()), state.st_mtime_ns, state.st_size)


@lru_cache(maxsize=8192)
def cached_selection_record(path_text: str, modified_ns: int, size: int) -> tuple[dict[str, object], str]:
    # The file state is part of the key, so a persistent Python caller sees a
    # revised vault record rather than a stale ranking cache.
    fields, body = read_markdown_record(Path(path_text))
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


def vault_relative(path: Path | None, vault: Path, resolved_paths: dict[Path, Path] | None = None) -> str:
    if not path:
        return ""

    def resolve_once(value: Path) -> Path:
        if resolved_paths is None:
            return value.resolve()
        if value not in resolved_paths:
            resolved_paths[value] = value.resolve()
        return resolved_paths[value]

    resolved_path = resolve_once(path)
    resolved_vault = resolve_once(vault)
    try:
        return resolved_path.relative_to(resolved_vault).as_posix()
    except ValueError:
        return str(resolved_path)


if __name__ == "__main__":
    raise SystemExit(main())
